// ApiClient.h : 通过 WinHTTP 调用 DGate 服务端接口
#pragma once

#include <functional>

// 一次接口调用的结果
struct ApiResult {
    bool    networkOk;   // HTTP 通信是否完成（false = 网络层失败）
    int     code;        // 服务端业务码，0 = 成功
    CString message;     // 服务端消息或本地网络错误描述
    CString token;       // 登录成功时返回的签名字符串
    int     uid;         // 登录成功时的用户 UID

    ApiResult() : networkOk(false), code(-1), uid(0) {}

    bool Ok() const { return networkOk && code == 0; }
};

// 版本查询结果
struct VersionResult {
    bool    networkOk;   // HTTP 通信是否完成（false = 网络层失败）
    int     code;        // 服务端业务码，0 = 成功；-1 = 响应异常
    CString message;
    int     baseline;    // 基线版本（完整客户端版本；本地低于此值须重下完整客户端）
    int     version;     // 当前版本（补丁叠加后的最新版本）
    unsigned long long zipSize;   // 更新包字节数（信息展示用）

    VersionResult() : networkOk(false), code(-1), baseline(0), version(0),
                      zipSize(0) {}
};

class ApiClient {
public:
    static ApiResult Register(const CStringW& serverUrl,
                              const CStringW& user, const CStringW& pass);

    static ApiResult Login(const CStringW& serverUrl,
                           const CStringW& user, const CStringW& pass);

    static ApiResult ChangePassword(const CStringW& serverUrl, const CStringW& user,
                                    const CStringW& oldPass, const CStringW& newPass);

    // 查询服务器基线版本号（短超时，供启动时更新检查）
    static VersionResult GetVersion(const CStringW& serverUrl);

    // GET 下载文件到 destPath（流式写盘）。progress(downloaded, total)
    // 在数据线程回调（total 为 0 表示服务端未提供总长）。失败返回 false
    // 并填充 error。
    static bool DownloadFile(const CStringW& serverUrl, const CStringW& path,
                             const CStringW& destPath,
                             const std::function<void(unsigned long long,
                                                      unsigned long long)>& progress,
                             CStringW& error);

private:
    static ApiResult PostJson(const CStringW& serverUrl, const CStringW& path,
                              const std::string& utf8Body);
    static std::string BuildRegisterBody(const CStringW& user, const CStringW& pass);
    static std::string BuildLoginBody(const CStringW& user, const CStringW& pass);
    static std::string BuildChangePasswordBody(const CStringW& user,
                                               const CStringW& oldPass,
                                               const CStringW& newPass);
    static std::string WideToUtf8(const CStringW& text);
    static std::string JsonEscape(const std::string& utf8Text);
    static CString Utf8ToWide(const std::string& utf8Text);
};
