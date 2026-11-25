// Theme.h : 启动器主题 —— 复刻管理后台深色 + 金色视觉（GDI/GDI+ 自绘）
// 颜色 token 与 server/web/assets/style.css 的 :root 保持一致。
#pragma once

// 调色板
namespace DGateTheme
{
    const COLORREF Card       = RGB(0x1A, 0x23, 0x38);  // --card 卡片底
    const COLORREF EditBg     = RGB(0x16, 0x1D, 0x2E);  // --bg-2 输入框底
    const COLORREF Line       = RGB(0x2A, 0x38, 0x54);  // --line 描边
    const COLORREF Text       = RGB(0xE6, 0xEC, 0xF7);  // --text 正文
    const COLORREF Muted      = RGB(0x8F, 0xA0, 0xBD);  // --muted 次要文字
    const COLORREF Accent     = RGB(0xE5, 0xA9, 0x39);  // --accent 金色
    const COLORREF Accent2    = RGB(0xF0, 0xC1, 0x68);  // --accent-2 hover 金
    const COLORREF AccentDark = RGB(0xC9, 0x8D, 0x24);  // 金色按钮渐变深端
    const COLORREF BtnText    = RGB(0x20, 0x15, 0x03);  // 金色按钮文字
    const COLORREF Err        = RGB(0xE5, 0x53, 0x4B);  // --err
    const COLORREF Ok         = RGB(0x4C, 0xC3, 0x8A);  // --ok
    const COLORREF HeaderTop  = RGB(0x13, 0x1A, 0x2B);  // 标题条渐变起
    const COLORREF HeaderBot  = RGB(0x0D, 0x13, 0x20);  // 标题条渐变止

    HBRUSH Solid(COLORREF color);          // 进程级缓存纯色刷
    int    HeaderHeight(const CWnd& wnd);  // 标题条高度（随对话框字体缩放）
}

// 自绘按钮：Primary=金色渐变主按钮；Ghost=描边幽灵按钮
// 仍是真实 Button 控件（BS_OWNERDRAW），BM_CLICK/焦点/Tab 均正常。
class CThemeButton : public CButton {
    DECLARE_MESSAGE_MAP()

public:
    enum Style { Primary, Ghost };

    CThemeButton() = default;

    void SetStyle(Style style) { m_style = style; }
    virtual void DrawItem(LPDRAWITEMSTRUCT lpDrawItemStruct) override;

protected:
    afx_msg void OnMouseMove(UINT nFlags, CPoint point);
    afx_msg void OnMouseLeave();

private:
    void TrackHover();

    Style m_style = Ghost;
    bool m_hover = false;
};

// 自绘复选框：深色卡片上的方块 + 金色勾选态（系统复选框在深底上会露白底）。
// 仍是真实 Button 控件（BS_OWNERDRAW），点击经反射 BN_CLICKED 自行翻转勾选态。
class CThemeCheckBox : public CButton {
    DECLARE_MESSAGE_MAP()

public:
    bool IsChecked() const { return m_checked; }
    void SetChecked(bool on) { m_checked = on; Invalidate(FALSE); }
    virtual void DrawItem(LPDRAWITEMSTRUCT lpDrawItemStruct) override;

protected:
    afx_msg void OnClickedReflect();

private:
    bool m_checked = false;
};

// 深色主题对话框基类：
// - 卡片底 + 渐变标题条（品牌 D+GATE 后缀、自绘关闭钮、HTCAPTION 拖动）
// - 输入框自绘直角边框（聚焦金色描边，配合 WM_CTLCOLOR_EDIT 深色底）
// 报错信息一律 AfxMessageBox 弹出，对话框内不放状态文字（用户约定）。
class CThemeDialog : public CDialog {
    DECLARE_MESSAGE_MAP()

public:
    CThemeDialog(UINT nIDTemplate, CWnd* pParentWnd = nullptr);
    virtual ~CThemeDialog() = default;

protected:
    // 标题条品牌后缀（如「登录器」），与金色 D + 白色 GATE 拼接绘制
    virtual CStringW HeaderCaption() const = 0;
    // 窗口文本（任务栏/FindWindow 用；主对话框必须保持 "DGate 登录器"）
    virtual CStringW WindowText() const = 0;
    // 需要自绘圆角边框的输入框控件 ID
    virtual void GetThemeEditIds(CUIntArray& ids) const = 0;
    // 是否绘制右上角关闭钮（更新对话框等不可中断窗口返回 false）
    virtual bool HasCloseButton() const { return true; }

    virtual BOOL OnInitDialog() override;
    afx_msg void OnPaint();
    afx_msg BOOL OnEraseBkgnd(CDC* pDC);
    afx_msg HBRUSH OnCtlColor(CDC* pDC, CWnd* pWnd, UINT nCtlColor);
    afx_msg LRESULT OnNcHitTest(CPoint point);
    afx_msg void OnMouseMove(UINT nFlags, CPoint point);
    afx_msg void OnMouseLeave();
    afx_msg void OnLButtonUp(UINT nFlags, CPoint point);
    afx_msg void OnClose();
    afx_msg void OnEditSetFocus(UINT nID);
    afx_msg void OnEditKillFocus(UINT nID);

private:
    void InvalidateEditFrame(UINT nID);
    void TrackHoverDialog();

    CRect m_closeRect;        // 关闭钮区域（客户区坐标，OnPaint 内计算）
    bool m_closeHover = false;
    bool m_tracked = false;
};
