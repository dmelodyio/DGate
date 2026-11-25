// ChangePwdDialog.h : 修改密码对话框
#pragma once

#include "Theme.h"

class CChangePwdDialog : public CThemeDialog {
    DECLARE_MESSAGE_MAP()

public:
    explicit CChangePwdDialog(CWnd* pParent = nullptr);

    enum { IDD = IDD_CHANGEPWD };

    CStringW m_server;   // 输入/输出：服务器地址
    CStringW m_user;     // 输入/输出：账号

protected:
    virtual void DoDataExchange(CDataExchange* pDX) override;
    virtual BOOL OnInitDialog() override;
    virtual CStringW HeaderCaption() const override { return L"修改密码"; }
    virtual CStringW WindowText() const override { return L"修改密码"; }
    virtual void GetThemeEditIds(CUIntArray& ids) const override
    {
        ids.Add(IDC_EDIT_USER);
        ids.Add(IDC_EDIT_OLDPASS);
        ids.Add(IDC_EDIT_NEWPASS);
        ids.Add(IDC_EDIT_NEWPASS2);
    }
    afx_msg void OnChange();

private:
    CThemeButton m_btnOk;
    CThemeButton m_btnCancel;
    CStringW m_oldPass;
    CStringW m_newPass;
    CStringW m_newPass2;
};
