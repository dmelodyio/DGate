// RegisterDialog.cpp
#include "stdafx.h"
#include "RegisterDialog.h"
#include "ApiClient.h"
#include "Validators.h"

BEGIN_MESSAGE_MAP(CRegisterDialog, CThemeDialog)
    ON_BN_CLICKED(IDOK, &CRegisterDialog::OnRegister)
END_MESSAGE_MAP()

CRegisterDialog::CRegisterDialog(CWnd* pParent)
    : CThemeDialog(IDD, pParent)
{
}

void CRegisterDialog::DoDataExchange(CDataExchange* pDX)
{
    CThemeDialog::DoDataExchange(pDX);
    DDX_Control(pDX, IDOK, m_btnOk);
    DDX_Control(pDX, IDCANCEL, m_btnCancel);
    DDX_Text(pDX, IDC_EDIT_USER, m_user);
    DDX_Text(pDX, IDC_EDIT_PASS, m_pass);
    DDX_Text(pDX, IDC_EDIT_PASS2, m_pass2);
}

BOOL CRegisterDialog::OnInitDialog()
{
    CThemeDialog::OnInitDialog();
    m_btnOk.SetStyle(CThemeButton::Primary);
    m_btnCancel.SetStyle(CThemeButton::Ghost);
    SendDlgItemMessage(IDC_EDIT_USER, EM_SETLIMITTEXT, 16, 0);
    SendDlgItemMessage(IDC_EDIT_PASS, EM_SETLIMITTEXT, 32, 0);
    SendDlgItemMessage(IDC_EDIT_PASS2, EM_SETLIMITTEXT, 32, 0);
    GetDlgItem(IDC_EDIT_USER)->SetFocus();
    return FALSE;
}

void CRegisterDialog::OnRegister()
{
    UpdateData(TRUE);
    m_user.Trim();

    if (!ValidUsername(m_user)) {
        AfxMessageBox(L"账号需为 6~16 位英文字母和数字。");
        return;
    }
    if (!ValidPassword(m_pass)) {
        AfxMessageBox(L"密码需为 8~32 位英文字母、数字和符号。");
        return;
    }
    if (m_pass != m_pass2) {
        AfxMessageBox(L"两次输入的密码不一致。");
        return;
    }

    CWaitCursor wait;

    ApiResult result = ApiClient::Register(m_server, m_user, m_pass);
    if (!result.networkOk) {
        AfxMessageBox(result.message);
        return;
    }
    if (!result.Ok()) {
        AfxMessageBox(L"注册失败：" + result.message);
        return;
    }

    // 注册成功弹窗（2026-11-24 用户要求；覆盖此前“成功静默”约定）
    AfxMessageBox(L"注册成功。", MB_ICONINFORMATION | MB_OK);
    EndDialog(IDOK);
}
