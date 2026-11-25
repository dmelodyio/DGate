// ChangePwdDialog.cpp
#include "stdafx.h"
#include "ChangePwdDialog.h"
#include "ApiClient.h"
#include "Validators.h"

BEGIN_MESSAGE_MAP(CChangePwdDialog, CThemeDialog)
    ON_BN_CLICKED(IDOK, &CChangePwdDialog::OnChange)
END_MESSAGE_MAP()

CChangePwdDialog::CChangePwdDialog(CWnd* pParent)
    : CThemeDialog(IDD, pParent)
{
}

void CChangePwdDialog::DoDataExchange(CDataExchange* pDX)
{
    CThemeDialog::DoDataExchange(pDX);
    DDX_Control(pDX, IDOK, m_btnOk);
    DDX_Control(pDX, IDCANCEL, m_btnCancel);
    DDX_Text(pDX, IDC_EDIT_USER, m_user);
    DDX_Text(pDX, IDC_EDIT_OLDPASS, m_oldPass);
    DDX_Text(pDX, IDC_EDIT_NEWPASS, m_newPass);
    DDX_Text(pDX, IDC_EDIT_NEWPASS2, m_newPass2);
}

BOOL CChangePwdDialog::OnInitDialog()
{
    CThemeDialog::OnInitDialog();
    m_btnOk.SetStyle(CThemeButton::Primary);
    m_btnCancel.SetStyle(CThemeButton::Ghost);
    SendDlgItemMessage(IDC_EDIT_USER, EM_SETLIMITTEXT, 16, 0);
    SendDlgItemMessage(IDC_EDIT_OLDPASS, EM_SETLIMITTEXT, 32, 0);
    SendDlgItemMessage(IDC_EDIT_NEWPASS, EM_SETLIMITTEXT, 32, 0);
    SendDlgItemMessage(IDC_EDIT_NEWPASS2, EM_SETLIMITTEXT, 32, 0);
    GetDlgItem(IDC_EDIT_USER)->SetFocus();
    return FALSE;
}

void CChangePwdDialog::OnChange()
{
    UpdateData(TRUE);
    m_user.Trim();

    if (!ValidUsername(m_user)) {
        AfxMessageBox(L"账号需为 6~16 位英文字母和数字。");
        return;
    }
    if (!ValidPassword(m_oldPass)) {
        AfxMessageBox(L"旧密码格式不正确。");
        return;
    }
    if (!ValidPassword(m_newPass)) {
        AfxMessageBox(L"新密码需为 8~32 位英文字母、数字和符号。");
        return;
    }
    if (m_newPass != m_newPass2) {
        AfxMessageBox(L"两次输入的新密码不一致。");
        return;
    }

    CWaitCursor wait;

    ApiResult result = ApiClient::ChangePassword(m_server, m_user, m_oldPass, m_newPass);
    if (!result.networkOk) {
        AfxMessageBox(result.message);
        return;
    }
    if (!result.Ok()) {
        AfxMessageBox(L"修改失败：" + result.message);
        return;
    }

    EndDialog(IDOK);
}
