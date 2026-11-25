// RegisterDialog.h : 注册对话框
#pragma once

#include "Theme.h"

class CRegisterDialog : public CThemeDialog {
    DECLARE_MESSAGE_MAP()

public:
    explicit CRegisterDialog(CWnd* pParent = nullptr);

    enum { IDD = IDD_REGISTER };

    // 输入：主界面当前服务器地址；输出：注册成功后回填的账号
    CStringW m_server;
    CStringW m_user;

protected:
    virtual void DoDataExchange(CDataExchange* pDX) override;
    virtual BOOL OnInitDialog() override;
    virtual CStringW HeaderCaption() const override { return L"注册账号"; }
    virtual CStringW WindowText() const override { return L"注册账号"; }
    virtual void GetThemeEditIds(CUIntArray& ids) const override
    {
        ids.Add(IDC_EDIT_USER);
        ids.Add(IDC_EDIT_PASS);
        ids.Add(IDC_EDIT_PASS2);
    }
    afx_msg void OnRegister();

private:
    CThemeButton m_btnOk;
    CThemeButton m_btnCancel;
    CStringW m_pass;
    CStringW m_pass2;
};
