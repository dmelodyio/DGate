// MainDialog.cpp : 登录成功后拉起游戏并注入插件
#include "stdafx.h"
#include "MainDialog.h"
#include "RegisterDialog.h"
#include "ChangePwdDialog.h"
#include "UpdateDialog.h"
#include "ApiClient.h"
#include "GameLauncher.h"
#include "Validators.h"
#include "Logger.h"
#include "Protocol.h"
#include "ZipExtract.h"

#include <shlwapi.h>

BEGIN_MESSAGE_MAP(CMainDialog, CThemeDialog)
    ON_BN_CLICKED(IDOK, &CMainDialog::OnLogin)
    ON_BN_CLICKED(IDC_BTN_REGISTER, &CMainDialog::OnRegister)
    ON_BN_CLICKED(IDC_BTN_CHANGEPWD, &CMainDialog::OnChangePassword)
    ON_WM_CTLCOLOR()
END_MESSAGE_MAP()

CMainDialog::CMainDialog(CWnd* pParent)
    : CThemeDialog(IDD, pParent)
{
}

void CMainDialog::DoDataExchange(CDataExchange* pDX)
{
    CThemeDialog::DoDataExchange(pDX);
    DDX_Control(pDX, IDOK, m_btnLogin);
    DDX_Control(pDX, IDC_BTN_REGISTER, m_btnRegister);
    DDX_Control(pDX, IDC_BTN_CHANGEPWD, m_btnChangePwd);
    DDX_Control(pDX, IDC_CHK_SAVE, m_chkSave);
    DDX_Text(pDX, IDC_EDIT_USER, m_user);
    DDX_Text(pDX, IDC_EDIT_PASS, m_pass);
}

BOOL CMainDialog::OnInitDialog()
{
    CThemeDialog::OnInitDialog();
    SetIcon(AfxGetApp()->LoadIconW(IDI_APPICON), TRUE);
    SetIcon(AfxGetApp()->LoadIconW(IDI_APPICON), FALSE);

    m_btnLogin.SetStyle(CThemeButton::Primary);
    m_btnRegister.SetStyle(CThemeButton::Ghost);
    m_btnChangePwd.SetStyle(CThemeButton::Ghost);

    m_server = LoadServerIni();

    // 登录界面出现前：清理上次升级遗留 + 检查更新（失败阻止登录）；
    // 版本检查同时采集服务器状态/版本号，显示在表单上方信息行
    ZipExtract::CleanupStaleOld();
    if (!CheckUpdate()) {
        EndDialog(IDCANCEL);
        return FALSE;
    }
    SetDlgItemTextW(IDC_STATIC_SERVERINFO, m_serverInfo);

    SendDlgItemMessage(IDC_EDIT_USER, EM_SETLIMITTEXT, 16, 0);
    SendDlgItemMessage(IDC_EDIT_PASS, EM_SETLIMITTEXT, 32, 0);
    LoadSavedAccount();   // [account] 已保存则预填账号密码并勾选复选框
    UpdateData(FALSE);

    GetDlgItem(IDC_EDIT_USER)->SetFocus();
    return FALSE;
}

// 信息行按在线状态着色（在线绿/离线红），其余控件沿用主题基类
HBRUSH CMainDialog::OnCtlColor(CDC* pDC, CWnd* pWnd, UINT nCtlColor)
{
    if (nCtlColor == CTLCOLOR_STATIC &&
            pWnd->GetDlgCtrlID() == IDC_STATIC_SERVERINFO) {
        pDC->SetTextColor(m_serverOnline ? DGateTheme::Ok : DGateTheme::Err);
        pDC->SetBkMode(TRANSPARENT);
        return DGateTheme::Solid(DGateTheme::Card);
    }
    return CThemeDialog::OnCtlColor(pDC, pWnd, nCtlColor);
}

// 登录前的游戏更新检查（双版本模型）：
// - 网络失败 -> 弹窗报错并阻止登录（用户约定：保证客户端版本一致）
// - 服务端为旧版本/无更新 -> 记日志后进登录
// - 本地 >= 当前版本 -> 已是最新，进登录
// - 本地 < 基线版本 -> 拒绝升级：提示重新下载完整客户端，阻止登录
// - 基线 <= 本地 < 当前 -> 弹出更新进度对话框，失败弹窗后阻止登录
bool CMainDialog::CheckUpdate()
{
    const int localVersion = LoadLocalVersion();
    VersionResult v = ApiClient::GetVersion(m_server);
    if (!v.networkOk) {
        m_serverOnline = false;
        m_serverInfo = L"服务器离线";
        Logger::Write(CStringW(L"更新检查失败（网络）：") + v.message);
        CStringW msg = L"更新检查失败：" + v.message;
        AfxMessageBox(msg, MB_ICONERROR | MB_OK);
        return false;
    }
    m_serverOnline = true;
    if (v.code != 0) {
        // 旧版服务端（无版本接口）或响应异常：按无更新处理
        m_serverInfo = L"服务器在线";
        Logger::Write(CStringW(L"更新检查跳过：") + v.message);
        return true;
    }
    m_serverInfo.Format(L"服务器在线 · 版本 v%d", v.version);
    CStringW ver;
    ver.Format(L"更新检查：服务器当前版本 v%d（基线 v%d），本地 v%d",
               v.version, v.baseline, localVersion);
    Logger::Write(ver);
    if (localVersion >= v.version)
        return true;   // 已是最新

    if (localVersion < v.baseline) {
        // 完整客户端之前的版本：增量包不可用，拒绝升级
        Logger::Write(L"本地版本低于基线版本，拒绝增量升级");
        AfxMessageBox(L"游戏版本过低，请重新下载完整客户端。",
                      MB_ICONWARNING | MB_OK);
        return false;
    }

    CUpdateDialog dlg(v.version, m_server, this);
    const INT_PTR r = dlg.DoModal();
    if (r != IDOK) {
        CStringW msg = L"游戏更新失败：" + dlg.m_error;
        AfxMessageBox(msg, MB_ICONERROR | MB_OK);
        return false;
    }
    return true;
}

CStringW CMainDialog::IniPath()
{
    wchar_t exePath[MAX_PATH] = { 0 };
    ::GetModuleFileNameW(nullptr, exePath, MAX_PATH);
    CStringW ini(exePath);
    int pos = ini.ReverseFind(L'\\');
    if (pos >= 0)
        ini = ini.Left(pos);
    return ini + L"\\DGateLauncher.ini";
}

void CMainDialog::WriteDefaultIni()
{
    static const wchar_t kDefault[] =
        L"[server]\r\n"
        L"url=http://127.0.0.1:5000\r\n"
        L"\r\n"
        L"[update]\r\n"
        L"; 本地客户端版本号（游戏升级自动维护，勿手动修改）\r\n"
        L"version=1\r\n"
        L"\r\n"
        L"[inject]\r\n"
        L"; 注入清单：逗号分隔，相对启动器所在目录，按给定顺序注入；留空则不注入任何 DLL\r\n"
        L"; 示例：dlls=ijl15.dll,Plugins\\aaa.dll\r\n"
        L"dlls=\r\n";

    HANDLE h = ::CreateFileW(IniPath(), GENERIC_WRITE, 0, nullptr,
                             CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE)
        return;
    DWORD written = 0;
    ::WriteFile(h, "\xff\xfe", 2, &written, nullptr);   // UTF-16LE BOM
    ::WriteFile(h, kDefault, static_cast<DWORD>(lstrlenW(kDefault) * sizeof(wchar_t)),
                &written, nullptr);
    ::CloseHandle(h);
}

CStringW CMainDialog::LoadServerIni()
{
    const CStringW ini = IniPath();
    const CStringW defaultUrl = L"http://127.0.0.1:5000";

    if (!::PathFileExistsW(ini)) {
        // ini 缺失：生成完整默认配置（[server] + [inject]）便于用户编辑
        WriteDefaultIni();
        return defaultUrl;
    }

    wchar_t buf[160] = { 0 };
    ::GetPrivateProfileStringW(L"server", L"url", L"", buf, 160, ini);
    CStringW url(buf);
    url.Trim();

    if (!url.IsEmpty())
        return url;

    ::WritePrivateProfileStringW(L"server", L"url", defaultUrl, ini);
    return defaultUrl;
}

std::vector<CStringW> CMainDialog::LoadInjectList()
{
    std::vector<CStringW> items;
    wchar_t buf[2048] = { 0 };
    ::GetPrivateProfileStringW(L"inject", L"dlls", L"", buf, 2048, IniPath());

    const CStringW list(buf);
    int pos = 0;
    for (;;) {
        CStringW item = list.Tokenize(L",;", pos);
        if (pos < 0)
            break;
        item.Trim();
        if (!item.IsEmpty())
            items.push_back(item);
    }
    return items;
}

// [account] 节：save/username/password（用户明令要求直接存 ini）
void CMainDialog::LoadSavedAccount()
{
    const CStringW ini = IniPath();
    if (::GetPrivateProfileIntW(L"account", L"save", 0, ini) != 1)
        return;
    wchar_t buf[64] = { 0 };
    ::GetPrivateProfileStringW(L"account", L"username", L"", buf, 64, ini);
    m_user = buf;
    ::GetPrivateProfileStringW(L"account", L"password", L"", buf, 64, ini);
    m_pass = buf;
    m_chkSave.SetChecked(true);
}

void CMainDialog::SaveSavedAccount(bool remember)
{
    const CStringW ini = IniPath();
    ::WritePrivateProfileStringW(L"account", L"save", remember ? L"1" : L"0", ini);
    if (remember) {
        ::WritePrivateProfileStringW(L"account", L"username", m_user, ini);
        ::WritePrivateProfileStringW(L"account", L"password", m_pass, ini);
    } else {
        // 未勾选：清除已存账号（传 nullptr 删除键）
        ::WritePrivateProfileStringW(L"account", L"username", nullptr, ini);
        ::WritePrivateProfileStringW(L"account", L"password", nullptr, ini);
    }
}

int CMainDialog::LoadLocalVersion()
{
    return ::GetPrivateProfileIntW(L"update", L"version", 0, IniPath());
}

void CMainDialog::SaveLocalVersion(int version)
{
    CStringW text;
    text.Format(L"%d", version);
    ::WritePrivateProfileStringW(L"update", L"version", text, IniPath());
    Logger::Write(CStringW(L"本地版本号已更新：v") + text);
}

void CMainDialog::OnLogin()
{
    UpdateData(TRUE);
    m_user.Trim();

    if (m_user.IsEmpty() || m_pass.IsEmpty()) {
        AfxMessageBox(L"请输入账号和密码。");
        return;
    }
    if (!ValidUsername(m_user)) {
        AfxMessageBox(L"账号需为 6~16 位英文字母和数字。");
        return;
    }
    if (!ValidPassword(m_pass)) {
        AfxMessageBox(L"密码需为 8~32 位英文字母、数字和符号。");
        return;
    }

    CWaitCursor wait;

    Logger::Write(CStringW(L"登录请求: server=") + m_server + L", user=" + m_user);
    ApiResult result = ApiClient::Login(m_server, m_user, m_pass);

    if (!result.networkOk) {
        Logger::Write(L"登录失败（网络）: " + result.message);
        AfxMessageBox(result.message);
        return;
    }
    if (!result.Ok()) {
        Logger::Write(L"登录失败（服务端）: " + result.message);
        AfxMessageBox(L"登录失败：" + result.message);
        return;
    }

    // 登录成功：记录 UID、92 字符基础字符串与服务端返回的 Base64 签名串
    CStringW uidText;
    uidText.Format(L"%u", result.uid);
    Logger::Write(L"登录成功: uid=" + uidText);
    Logger::Write(L"92字符基础字符串: " + Protocol::BuildTokenBaseString(
        static_cast<unsigned int>(result.uid)));
    Logger::Write(L"登录token(Base64): " + result.token);

    // 按“保存用户名和密码”复选框落盘/清除（仅登录成功时写入，避免错误密码被记住）
    SaveSavedAccount(m_chkSave.IsChecked());

    // 隐藏界面后拉起游戏
    ShowWindow(SW_HIDE);

    const std::vector<CStringW> injectList = LoadInjectList();
    if (injectList.empty())
        Logger::Write(L"注入清单为空（[inject] dlls 未配置），仅启动游戏");
    else {
        CStringW joined;
        for (const auto& item : injectList)
            joined += item + L", ";
        Logger::Write(L"注入清单: " + joined.TrimRight(L", "));
    }

    CString report;
    bool launched = GameLauncher::LaunchAndInject(result.token, injectList, report);
    // report 为空 = 全部成功：静默退出（成功信息见 launcher.log）；
    // 非空 = 拉起失败或存在注入失败，弹窗提示。
    if (!report.IsEmpty())
        AfxMessageBox(report, launched ? MB_ICONWARNING : MB_ICONERROR);

    EndDialog(launched ? IDOK : IDRETRY);   // 拉起成功后关闭自身
}

void CMainDialog::OnRegister()
{
    UpdateData(TRUE);   // 同步当前账号便于回填
    CRegisterDialog dlg(this);
    dlg.m_server = m_server;
    dlg.m_user = m_user;
    if (dlg.DoModal() != IDOK)
        return;

    m_user = dlg.m_user;
    m_pass.Empty();
    UpdateData(FALSE);
}

void CMainDialog::OnChangePassword()
{
    UpdateData(TRUE);
    CChangePwdDialog dlg(this);
    dlg.m_server = m_server;
    dlg.m_user = m_user;
    if (dlg.DoModal() != IDOK)
        return;

    UpdateData(FALSE);
    m_pass.Empty();
}
