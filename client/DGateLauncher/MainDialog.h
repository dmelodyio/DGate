// MainDialog.h : 主登录对话框（配置来自 DGateLauncher.ini，不在界面输入）
#pragma once

#include "Theme.h"

class CMainDialog : public CThemeDialog {
    DECLARE_MESSAGE_MAP()

public:
    explicit CMainDialog(CWnd* pParent = nullptr);

    enum { IDD = IDD_LOGIN };

    CStringW m_server;   // 来自 DGateLauncher.ini 的服务器地址

    // 本地客户端版本号（[update] version；游戏升级流程读写）
    static int LoadLocalVersion();
    static void SaveLocalVersion(int version);

protected:
    virtual void DoDataExchange(CDataExchange* pDX) override;
    virtual BOOL OnInitDialog() override;
    virtual CStringW HeaderCaption() const override { return L"登录器"; }
    virtual CStringW WindowText() const override { return L"DGate 登录器"; }
    virtual void GetThemeEditIds(CUIntArray& ids) const override
    {
        ids.Add(IDC_EDIT_USER);
        ids.Add(IDC_EDIT_PASS);
    }

    afx_msg void OnLogin();
    afx_msg void OnRegister();
    afx_msg void OnChangePassword();
    afx_msg HBRUSH OnCtlColor(CDC* pDC, CWnd* pWnd, UINT nCtlColor);

private:
    static CStringW IniPath();
    // 首次运行生成默认 DGateLauncher.ini（[server] + [update] + [inject] 模板）
    static void WriteDefaultIni();
    // 读取 [server] url；ini 缺失或未配置时生成默认值
    static CStringW LoadServerIni();
    // 读取 [inject] dlls（逗号/分号分隔的注入清单，相对启动器目录）
    static std::vector<CStringW> LoadInjectList();
    // [account] 节：已保存的账号密码预填 + 复选框勾选态
    void LoadSavedAccount();
    // 仅登录成功时落盘：勾选=写入账号密码；未勾选=清除已存账号
    void SaveSavedAccount(bool remember);
    // 登录前更新检查（见 MainDialog.cpp 注释）：false = 阻止进入登录
    bool CheckUpdate();

    CThemeButton m_btnLogin;
    CThemeButton m_btnRegister;
    CThemeButton m_btnChangePwd;
    CThemeCheckBox m_chkSave;      // 保存用户名和密码（ini [account] 节）
    CStringW m_user;
    CStringW m_pass;
    bool m_serverOnline = false;   // 服务端是否可达（版本检查结果）
    CStringW m_serverInfo;         // 登录窗顶部信息行文本
};
