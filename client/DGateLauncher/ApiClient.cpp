// ApiClient.cpp : WinHTTP POST/GET + JSON 解析
#include "stdafx.h"
#include "ApiClient.h"
#include "MiniJson.h"

#include <winhttp.h>
#pragma comment(lib, "winhttp.lib")

namespace {

// 用 WinHttpCrackUrl 解析 http://host:port 或 https://host:port
bool CrackServerUrl(const CStringW& url, CStringW& host, INTERNET_PORT& port,
                    bool& secure)
{
    URL_COMPONENTSW parts = { sizeof(URL_COMPONENTSW) };
    wchar_t hostBuf[256] = { 0 };
    parts.lpszHostName = hostBuf;
    parts.dwHostNameLength = _countof(hostBuf);
    if (!WinHttpCrackUrl(url, url.GetLength(), 0, &parts))
        return false;
    if (parts.nScheme != INTERNET_SCHEME_HTTP &&
        parts.nScheme != INTERNET_SCHEME_HTTPS)
        return false;
    secure = (parts.nScheme == INTERNET_SCHEME_HTTPS);
    host = hostBuf;
    port = parts.nPort ? parts.nPort
                       : (secure ? INTERNET_DEFAULT_HTTPS_PORT
                                 : INTERNET_DEFAULT_HTTP_PORT);
    return true;
}

// WinHTTP 会话/连接/请求句柄链 RAII：析构时按序关闭
class HttpHandles {
public:
    HINTERNET session = nullptr;
    HINTERNET connect = nullptr;
    HINTERNET request = nullptr;
    bool secure = false;

    ~HttpHandles()
    {
        if (request)
            ::WinHttpCloseHandle(request);
        if (connect)
            ::WinHttpCloseHandle(connect);
        if (session)
            ::WinHttpCloseHandle(session);
    }
};

// 解析服务器地址并打开会话/连接/请求链。
// timeouts = {解析, 连接, 发送, 接收}（毫秒）。失败时填充 error 返回 false
//（RAII 保证已创建的句柄被关闭）。
bool OpenHttp(const CStringW& serverUrl, const wchar_t* method,
              const wchar_t* path, const DWORD (&timeouts)[4],
              HttpHandles& h, CStringW& error)
{
    CStringW host;
    INTERNET_PORT port = 80;
    CStringW trimmed = serverUrl;
    trimmed.Trim();
    if (!CrackServerUrl(trimmed, host, port, h.secure)) {
        error = L"服务器地址无效（应为 http:// 或 https://IP:端口 形式）";
        return false;
    }

    h.session = ::WinHttpOpen(L"DGateLauncher/1.0",
                              WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                              WINHTTP_NO_PROXY_NAME,
                              WINHTTP_NO_PROXY_BYPASS, 0);
    if (!h.session) {
        error = L"初始化网络组件失败";
        return false;
    }
    ::WinHttpSetTimeouts(h.session, timeouts[0], timeouts[1],
                         timeouts[2], timeouts[3]);

    h.connect = ::WinHttpConnect(h.session, host, port, 0);
    if (!h.connect) {
        error = L"无法连接服务器";
        return false;
    }

    h.request = ::WinHttpOpenRequest(
        h.connect, method, path, nullptr, WINHTTP_NO_REFERER,
        WINHTTP_DEFAULT_ACCEPT_TYPES, h.secure ? WINHTTP_FLAG_SECURE : 0);
    if (!h.request) {
        error = L"创建请求失败";
        return false;
    }
    return true;
}

// 自签名/名称不匹配证书容错：内网部署常见，出错后忽略证书错误重试一次
bool RetryWithRelaxedCert(HINTERNET hRequest, const std::string& utf8Body,
                          DWORD& lastError)
{
    lastError = ::GetLastError();
    // ERROR_WINHTTP_SECURE_CERT_DATE_INVALID(12037) ~ ERROR_WINHTTP_SECURE_FAILURE(12175)
    if (lastError < ERROR_WINHTTP_SECURE_CERT_DATE_INVALID ||
        lastError > ERROR_WINHTTP_SECURE_FAILURE)
        return false;

    DWORD flags = SECURITY_FLAG_IGNORE_UNKNOWN_CA |
                  SECURITY_FLAG_IGNORE_CERT_CN_INVALID |
                  SECURITY_FLAG_IGNORE_CERT_DATE_INVALID |
                  SECURITY_FLAG_IGNORE_CERT_WRONG_USAGE;
    if (!::WinHttpSetOption(hRequest, WINHTTP_OPTION_SECURITY_FLAGS,
                            &flags, sizeof(flags)))
        return false;
    return ::WinHttpSendRequest(hRequest, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                                const_cast<char*>(utf8Body.data()),
                                static_cast<DWORD>(utf8Body.size()),
                                static_cast<DWORD>(utf8Body.size()), 0) &&
           ::WinHttpReceiveResponse(hRequest, nullptr);
}

// 发送请求并接收响应；headers 为空串时不带附加头；body 为空串时按
// WINHTTP_NO_REQUEST_DATA 发送。HTTPS 证书错误时忽略证书错误重试一次。
bool SendAndReceive(const HttpHandles& h, const std::string& utf8Body,
                    const CStringW& headers)
{
    const bool hasBody = !utf8Body.empty();
    LPCWSTR extraHeaders = headers.IsEmpty()
        ? WINHTTP_NO_ADDITIONAL_HEADERS : static_cast<LPCWSTR>(headers);
    DWORD headersLen = headers.IsEmpty() ? 0
        : static_cast<DWORD>(headers.GetLength());
    BOOL sent = ::WinHttpSendRequest(
        h.request, extraHeaders, headersLen,
        hasBody ? const_cast<char*>(utf8Body.data()) : WINHTTP_NO_REQUEST_DATA,
        static_cast<DWORD>(utf8Body.size()),
        static_cast<DWORD>(utf8Body.size()), 0);
    bool ok = sent && ::WinHttpReceiveResponse(h.request, nullptr);
    if (!ok && h.secure) {
        DWORD ignoreErr = 0;
        ok = RetryWithRelaxedCert(h.request, utf8Body, ignoreErr);
    }
    return ok;
}

// 读取完整响应体（分块循环，直到无数据可读）
std::string ReadResponseBody(HINTERNET hRequest)
{
    std::string body;
    for (;;) {
        DWORD available = 0;
        if (!::WinHttpQueryDataAvailable(hRequest, &available) || available == 0)
            break;
        std::vector<char> chunk(available);
        DWORD readBytes = 0;
        if (!::WinHttpReadData(hRequest, chunk.data(), available, &readBytes))
            break;
        if (readBytes == 0)
            break;
        body.append(chunk.data(), readBytes);
    }
    return body;
}

// WinHTTP 错误码 -> 友好提示（超时文案与通用格式由调用方按场景给出）
CStringW WinHttpErrorText(DWORD err, const CStringW& timeoutMsg,
                          const CStringW& genericFmt)
{
    if (err == ERROR_WINHTTP_TIMEOUT)
        return timeoutMsg;
    if (err == ERROR_WINHTTP_CANNOT_CONNECT)
        return L"无法连接到服务器，请确认服务端已启动";
    if (err == ERROR_WINHTTP_NAME_NOT_RESOLVED)
        return L"无法解析服务器地址";
    CStringW msg;
    msg.Format(genericFmt, err);
    return msg;
}

} // namespace

std::string ApiClient::WideToUtf8(const CStringW& text)
{
    if (text.IsEmpty())
        return std::string();
    int bytes = ::WideCharToMultiByte(CP_UTF8, 0, text, text.GetLength(),
                                      nullptr, 0, nullptr, nullptr);
    std::string out(static_cast<size_t>(bytes), '\0');
    ::WideCharToMultiByte(CP_UTF8, 0, text, text.GetLength(), &out[0], bytes,
                          nullptr, nullptr);
    return out;
}

CString ApiClient::Utf8ToWide(const std::string& utf8Text)
{
    if (utf8Text.empty())
        return CString();
    int chars = ::MultiByteToWideChar(CP_UTF8, 0, utf8Text.data(),
                                      static_cast<int>(utf8Text.size()),
                                      nullptr, 0);
    CStringW out;
    wchar_t* buf = out.GetBuffer(chars);
    ::MultiByteToWideChar(CP_UTF8, 0, utf8Text.data(),
                          static_cast<int>(utf8Text.size()), buf, chars);
    out.ReleaseBuffer(chars);
    return out;
}

std::string ApiClient::JsonEscape(const std::string& text)
{
    std::string out;
    out.reserve(text.size() + 8);
    for (unsigned char c : text) {
        switch (c) {
        case '"':  out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\b': out += "\\b"; break;
        case '\f': out += "\\f"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default:
            if (c < 0x20) {
                char buf[8];
                sprintf_s(buf, "\\u%04x", c);
                out += buf;
            } else {
                out.push_back(static_cast<char>(c));
            }
        }
    }
    return out;
}

std::string ApiClient::BuildRegisterBody(const CStringW& user, const CStringW& pass)
{
    std::string u = JsonEscape(WideToUtf8(user));
    std::string p = JsonEscape(WideToUtf8(pass));
    return "{\"username\":\"" + u + "\",\"password\":\"" + p + "\"}";
}

std::string ApiClient::BuildLoginBody(const CStringW& user, const CStringW& pass)
{
    return BuildRegisterBody(user, pass);
}

std::string ApiClient::BuildChangePasswordBody(const CStringW& user,
                                               const CStringW& oldPass,
                                               const CStringW& newPass)
{
    std::string u = JsonEscape(WideToUtf8(user));
    std::string o = JsonEscape(WideToUtf8(oldPass));
    std::string n = JsonEscape(WideToUtf8(newPass));
    return "{\"username\":\"" + u + "\",\"old_password\":\"" + o +
           "\",\"new_password\":\"" + n + "\"}";
}

ApiResult ApiClient::PostJson(const CStringW& serverUrl, const CStringW& path,
                              const std::string& utf8Body)
{
    ApiResult result;
    static const DWORD kTimeouts[] = { 5000, 5000, 10000, 15000 };
    HttpHandles h;
    if (!OpenHttp(serverUrl, L"POST", path, kTimeouts, h, result.message))
        return result;

    const CStringW headers =
        L"Content-Type: application/json\r\nAccept: application/json\r\n";
    if (!SendAndReceive(h, utf8Body, headers)) {
        result.message = WinHttpErrorText(
            ::GetLastError(),
            L"请求超时，请检查服务器地址与网络",
            L"网络请求失败（错误码 %u）");
        return result;
    }

    const std::string body = ReadResponseBody(h.request);

    minijson::Value root;
    if (!minijson::Parse(body, root) || root.type != minijson::Value::OBJ) {
        result.networkOk = true;
        result.code = -1;
        result.message = L"服务端响应异常（无法解析的返回内容）";
        return result;
    }

    result.networkOk = true;
    result.code = root.at("code").asInt();
    result.message = Utf8ToWide(root.at("message").asStr());
    const minijson::Value& data = root.at("data");
    if (data.type == minijson::Value::OBJ) {
        result.token = Utf8ToWide(data.at("token").asStr());
        result.uid = data.at("uid").asInt();
    }
    return result;
}

ApiResult ApiClient::Register(const CStringW& serverUrl,
                              const CStringW& user, const CStringW& pass)
{
    return PostJson(serverUrl, L"/api/client/register",
                    BuildRegisterBody(user, pass));
}

ApiResult ApiClient::Login(const CStringW& serverUrl,
                           const CStringW& user, const CStringW& pass)
{
    return PostJson(serverUrl, L"/api/client/login", BuildLoginBody(user, pass));
}

ApiResult ApiClient::ChangePassword(const CStringW& serverUrl, const CStringW& user,
                                    const CStringW& oldPass, const CStringW& newPass)
{
    return PostJson(serverUrl, L"/api/client/password",
                    BuildChangePasswordBody(user, oldPass, newPass));
}

VersionResult ApiClient::GetVersion(const CStringW& serverUrl)
{
    VersionResult result;
    // 启动时的版本检查用短超时，避免服务端异常时长时间白屏
    static const DWORD kTimeouts[] = { 3000, 3000, 5000, 8000 };
    HttpHandles h;
    if (!OpenHttp(serverUrl, L"GET", L"/api/client/version", kTimeouts, h,
                  result.message))
        return result;

    const std::string emptyBody;
    const bool ok = SendAndReceive(h, emptyBody, CStringW());
    const std::string body = ok ? ReadResponseBody(h.request) : std::string();

    if (!ok) {
        result.message = WinHttpErrorText(
            ::GetLastError(),
            L"请求超时，请检查服务器地址与网络",
            L"网络请求失败（错误码 %u）");
        return result;
    }

    // 404：服务端为旧版本（无版本接口），按无更新处理
    if (body.find("\"current\"") == std::string::npos &&
            body.find("\"version\"") == std::string::npos) {
        result.networkOk = true;
        result.code = -1;
        result.message = L"服务端暂不支持自动更新";
        return result;
    }

    minijson::Value root;
    if (!minijson::Parse(body, root) || root.type != minijson::Value::OBJ) {
        result.networkOk = true;
        result.code = -1;
        result.message = L"服务端响应异常";
        return result;
    }

    result.networkOk = true;
    result.code = root.at("code").asInt();
    result.message = Utf8ToWide(root.at("message").asStr());
    const minijson::Value& data = root.at("data");
    if (data.type == minijson::Value::OBJ) {
        // 双版本模型：current + baseline（旧服务端无 baseline 时为 0，
        // 任何本地版本都不会低于 0，退化为只比对当前版本）
        result.version = data.at("current").asInt();
        if (result.version == 0)
            result.version = data.at("version").asInt();   // 旧格式兼容
        result.baseline = data.at("baseline").asInt();
        result.zipSize = static_cast<unsigned long long>(data.at("size").asInt());
    }
    return result;
}

bool ApiClient::DownloadFile(const CStringW& serverUrl, const CStringW& path,
                             const CStringW& destPath,
                             const std::function<void(unsigned long long,
                                                      unsigned long long)>& progress,
                             CStringW& error)
{
    // 大文件下载：单次读超时放宽到 30 秒
    static const DWORD kTimeouts[] = { 5000, 5000, 10000, 30000 };
    HttpHandles h;
    if (!OpenHttp(serverUrl, L"GET", path, kTimeouts, h, error))
        return false;

    if (!SendAndReceive(h, std::string(), CStringW())) {
        error = WinHttpErrorText(::GetLastError(),
                                 L"下载超时，请检查网络后重试",
                                 L"下载失败（错误码 %u）");
        return false;
    }

    // 校验 HTTP 状态码（200 才继续）
    DWORD statusCode = 0;
    DWORD statusCodeSize = sizeof(statusCode);
    BOOL haveStatus = ::WinHttpQueryHeaders(
        h.request,
        WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
        WINHTTP_HEADER_NAME_BY_INDEX, &statusCode, &statusCodeSize,
        WINHTTP_NO_HEADER_INDEX);
    if (!haveStatus || statusCode != 200) {
        if (haveStatus && statusCode == 404)
            error = L"服务器上暂无更新包";
        else
            error.Format(L"下载失败（HTTP %u）",
                         haveStatus ? statusCode : 0);
        return false;
    }

    // Content-Length（可能缺失：0 表示未知总长）
    unsigned long long total = 0;
    wchar_t lenBuf[32] = {0};
    DWORD lenSize = sizeof(lenBuf);
    if (::WinHttpQueryHeaders(h.request, WINHTTP_QUERY_CONTENT_LENGTH,
                              WINHTTP_HEADER_NAME_BY_INDEX, lenBuf, &lenSize,
                              WINHTTP_NO_HEADER_INDEX)) {
        total = _wcstoui64(lenBuf, nullptr, 10);
    }

    // 流式写盘
    FILE* fp = nullptr;
    if (_wfopen_s(&fp, destPath, L"wb") != 0 || fp == nullptr) {
        error = L"无法创建临时文件：" + destPath;
        return false;
    }

    unsigned long long downloaded = 0;
    bool readOk = true;
    for (;;) {
        DWORD available = 0;
        if (!::WinHttpQueryDataAvailable(h.request, &available)) {
            readOk = false;
            break;
        }
        if (available == 0)
            break;
        std::vector<char> chunk(available);
        DWORD readBytes = 0;
        if (!::WinHttpReadData(h.request, chunk.data(), available, &readBytes)) {
            readOk = false;
            break;
        }
        if (readBytes == 0)
            break;
        if (fwrite(chunk.data(), 1, readBytes, fp) != readBytes) {
            readOk = false;
            break;
        }
        downloaded += readBytes;
        if (progress)
            progress(downloaded, total);
    }
    fclose(fp);

    if (!readOk) {
        error = L"下载中断，请检查网络后重试";
        ::DeleteFileW(destPath);
        return false;
    }
    if (total > 0 && downloaded != total) {
        error.Format(L"下载不完整（%llu / %llu 字节）", downloaded, total);
        ::DeleteFileW(destPath);
        return false;
    }
    return true;
}
