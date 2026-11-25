// Theme.cpp : 启动器主题绘制实现（GDI 绘文字 + GDI+ 绘渐变/圆角）
#include "stdafx.h"
#include "Theme.h"

#include <gdiplus.h>
#pragma comment(lib, "gdiplus.lib")

using namespace DGateTheme;
using Gdiplus::REAL;

// ---------------------------------------------------------------- 辅助

namespace {

Gdiplus::Color GColor(COLORREF c, BYTE alpha = 255)
{
    return Gdiplus::Color(alpha, GetRValue(c), GetGValue(c), GetBValue(c));
}

// 基于对话框字体派生（加粗/放大）；调用方负责 delete
CFont* DerivedFont(const CWnd& wnd, bool bold, double scale = 1.0)
{
    LOGFONTW lf = { sizeof(lf) };
    lf.lfHeight = -12;   // 兜底：9pt
    lstrcpyW(lf.lfFaceName, L"Microsoft YaHei UI");
    HFONT hf = (HFONT)::SendMessageW(wnd.GetSafeHwnd(), WM_GETFONT, 0, 0);
    if (hf)
        ::GetObjectW(hf, sizeof(lf), &lf);
    if (bold)
        lf.lfWeight = FW_BOLD;
    lf.lfHeight = static_cast<LONG>(lf.lfHeight * scale);
    auto* font = new CFont();
    font->CreateFontIndirectW(&lf);
    return font;
}

}   // namespace

namespace DGateTheme {

// 进程级缓存纯色刷（进程退出时由系统回收）
HBRUSH Solid(COLORREF color)
{
    struct Entry { COLORREF c; HBRUSH h; };
    static std::vector<Entry> cache;
    for (const auto& e : cache)
        if (e.c == color)
            return e.h;
    HBRUSH h = ::CreateSolidBrush(color);
    cache.push_back({ color, h });
    return h;
}

int HeaderHeight(const CWnd& wnd)
{
    LOGFONTW lf = {};
    HFONT hf = (HFONT)::SendMessageW(wnd.GetSafeHwnd(), WM_GETFONT, 0, 0);
    if (hf && ::GetObjectW(hf, sizeof(lf), &lf) && lf.lfHeight < 0)
        return -lf.lfHeight * 2 + 14;   // 9pt@96dpi 下约 38px，随 DPI 缩放
    return 38;
}

}   // namespace DGateTheme

// ---------------------------------------------------------------- 自绘按钮

BEGIN_MESSAGE_MAP(CThemeButton, CButton)
    ON_WM_MOUSEMOVE()
    ON_WM_MOUSELEAVE()
END_MESSAGE_MAP()

void CThemeButton::TrackHover()
{
    TRACKMOUSEEVENT tme = { sizeof(tme), TME_LEAVE, GetSafeHwnd(), 0 };
    ::TrackMouseEvent(&tme);
}

void CThemeButton::OnMouseMove(UINT nFlags, CPoint point)
{
    if (!m_hover) {
        m_hover = true;
        Invalidate(FALSE);
        TrackHover();
    }
    CButton::OnMouseMove(nFlags, point);
}

void CThemeButton::OnMouseLeave()
{
    if (m_hover) {
        m_hover = false;
        Invalidate(FALSE);
    }
}

void CThemeButton::DrawItem(LPDRAWITEMSTRUCT lpDIS)
{
    if (!lpDIS || lpDIS->CtlType != ODT_BUTTON)
        return;
    CDC dc;
    dc.Attach(lpDIS->hDC);
    const CRect rc(lpDIS->rcItem);
    const bool pressed = (lpDIS->itemState & ODS_SELECTED) != 0;
    const bool disabled = (lpDIS->itemState & ODS_DISABLED) != 0;

    CStringW caption;
    GetWindowTextW(caption);

    Gdiplus::Graphics g(dc.GetSafeHdc());
    const Gdiplus::Rect grc(rc.left, rc.top, rc.Width(), rc.Height());

    dc.SetBkMode(TRANSPARENT);
    if (m_style == Primary) {
        COLORREF c1 = disabled ? Muted : (pressed ? AccentDark : (m_hover ? Accent2 : Accent));
        COLORREF c2 = disabled ? Muted : (pressed ? RGB(0xA8, 0x78, 0x18) : AccentDark);
        Gdiplus::RectF rf(REAL(rc.left), REAL(rc.top), REAL(rc.Width()), REAL(rc.Height()));
        Gdiplus::LinearGradientBrush brush(rf, GColor(c1), GColor(c2), 135.0f);
        g.FillRectangle(&brush, grc);

        dc.SetTextColor(disabled ? Muted : BtnText);
        CFont* font = DerivedFont(*this, true);
        CFont* old = dc.SelectObject(font);
        CRect rcText(rc);
        dc.DrawTextW(caption, rcText, DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
        dc.SelectObject(old);
        delete font;
    } else {   // Ghost
        dc.FillSolidRect(rc, Card);
        const COLORREF border = m_hover && !disabled ? Accent : Line;
        dc.FrameRect(&rc, CBrush::FromHandle(Solid(border)));   // 像素精确，四边完整
        dc.SetTextColor(disabled ? Muted : (m_hover ? Accent : (pressed ? Text : Muted)));
        CRect rcText(rc);
        dc.DrawTextW(caption, rcText, DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
    }
    dc.Detach();
}

// ---------------------------------------------------------------- 自绘复选框

BEGIN_MESSAGE_MAP(CThemeCheckBox, CButton)
    ON_CONTROL_REFLECT(BN_CLICKED, &CThemeCheckBox::OnClickedReflect)
END_MESSAGE_MAP()

void CThemeCheckBox::OnClickedReflect()
{
    SetChecked(!IsChecked());
}

void CThemeCheckBox::DrawItem(LPDRAWITEMSTRUCT lpDIS)
{
    if (!lpDIS || lpDIS->CtlType != ODT_BUTTON)
        return;
    CDC dc;
    dc.Attach(lpDIS->hDC);
    const CRect rc(lpDIS->rcItem);
    const bool focused = (lpDIS->itemState & ODS_FOCUS) != 0;

    CStringW caption;
    GetWindowTextW(caption);

    // 全底填充：owner-draw 未绘制区域会露出系统白底，必须先铺满卡片色
    dc.FillSolidRect(rc, Card);

    // 方块（13px，垂直居中）：未选=暗底描边；选中=金色填充 + 深色对勾
    const int box = 13;
    const int cy = (rc.top + rc.bottom) / 2;
    CRect rcBox(rc.left, cy - box / 2, rc.left + box, cy - box / 2 + box);
    dc.FillSolidRect(rcBox, EditBg);
    dc.FrameRect(&rcBox, CBrush::FromHandle(Solid(focused ? Accent : Line)));
    if (IsChecked()) {
        CRect rcFill(rcBox);
        rcFill.DeflateRect(2, 2);
        dc.FillSolidRect(rcFill, Accent);
        CPen pen(PS_SOLID, 2, BtnText);
        CPen* old = dc.SelectObject(&pen);
        dc.MoveTo(rcFill.left + 2, rcFill.top + rcFill.Height() / 2);
        dc.LineTo(rcFill.left + rcFill.Width() / 2 - 1, rcFill.bottom - 3);
        dc.LineTo(rcFill.right - 2, rcFill.top + 2);
        dc.SelectObject(old);
    }

    dc.SetBkMode(TRANSPARENT);
    dc.SetTextColor(Text);
    CRect rcText(rc.left + box + 8, rc.top, rc.right, rc.bottom);
    dc.DrawTextW(caption, rcText, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
    dc.Detach();
}

// ---------------------------------------------------------------- 主题对话框

BEGIN_MESSAGE_MAP(CThemeDialog, CDialog)
    ON_WM_PAINT()
    ON_WM_ERASEBKGND()
    ON_WM_CTLCOLOR()
    ON_WM_NCHITTEST()
    ON_WM_MOUSEMOVE()
    ON_WM_MOUSELEAVE()
    ON_WM_LBUTTONUP()
    ON_WM_CLOSE()
    ON_CONTROL_RANGE(EN_SETFOCUS, IDC_EDIT_USER, IDC_EDIT_NEWPASS2, &CThemeDialog::OnEditSetFocus)
    ON_CONTROL_RANGE(EN_KILLFOCUS, IDC_EDIT_USER, IDC_EDIT_NEWPASS2, &CThemeDialog::OnEditKillFocus)
END_MESSAGE_MAP()

CThemeDialog::CThemeDialog(UINT nIDTemplate, CWnd* pParentWnd)
    : CDialog(nIDTemplate, pParentWnd)
{
}

BOOL CThemeDialog::OnInitDialog()
{
    BOOL ok = CDialog::OnInitDialog();
    SetWindowTextW(WindowText());
    return ok;
}

BOOL CThemeDialog::OnEraseBkgnd(CDC* /*pDC*/)
{
    return TRUE;   // 背景统一在 OnPaint 的内存 DC 中绘制，避免闪烁
}

void CThemeDialog::OnPaint()
{
    CPaintDC dc(this);
    CRect rc;
    GetClientRect(&rc);
    const int headerH = HeaderHeight(*this);

    CDC mem;
    mem.CreateCompatibleDC(&dc);
    CBitmap bm;
    bm.CreateCompatibleBitmap(&dc, rc.Width(), rc.Height());
    CBitmap* oldBm = mem.SelectObject(&bm);

    mem.FillSolidRect(rc, Card);

    Gdiplus::Graphics g(mem.GetSafeHdc());

    // —— 标题条（垂直渐变 + 底线）与整窗描边 ——
    {
        Gdiplus::RectF hdr(0.f, 0.f, REAL(rc.Width()), REAL(headerH));
        Gdiplus::LinearGradientBrush brush(hdr, GColor(HeaderTop), GColor(HeaderBot), 90.f);
        g.FillRectangle(&brush, hdr);
    }
    // 1px 描边统一用 GDI（GDI+ 画笔居中对齐会裁掉右/下边框的半像素）
    mem.FillSolidRect(0, headerH - 1, rc.Width(), 1, Line);
    mem.FrameRect(&rc, CBrush::FromHandle(Solid(Line)));

    // —— 品牌：D(金) GATE(白) + 功能后缀(次要色) ——
    CStringW suffix = HeaderCaption();
    CFont* bold = DerivedFont(*this, true, 1.5);
    CFont* regular = DerivedFont(*this, false);
    CFont* oldFont = mem.SelectObject(bold);
    mem.SetBkMode(TRANSPARENT);
    int tx = 16;
    CSize szD = mem.GetTextExtent(L"D");
    int ty = (headerH - szD.cy) / 2;
    mem.SetTextColor(Accent);
    mem.TextOutW(tx, ty, L"D");
    tx += szD.cx;
    mem.SetTextColor(Text);
    CSize szGate = mem.GetTextExtent(L"GATE");
    mem.TextOutW(tx, ty, L"GATE");
    tx += szGate.cx + 12;
    mem.SelectObject(regular);
    mem.SetTextColor(Muted);
    CSize szSuf = mem.GetTextExtent(suffix);
    mem.TextOutW(tx, (headerH - szSuf.cy) / 2, suffix);
    mem.SelectObject(oldFont);
    delete bold;
    delete regular;

    // —— 关闭钮（右上，✕ 两笔线段；不可中断窗口不绘制）——
    m_closeRect.SetRectEmpty();
    if (HasCloseButton()) {
        const int size = headerH - 24;
        const int margin = 12;
        m_closeRect = CRect(rc.right - size - margin, (headerH - size) / 2,
                            rc.right - margin, (headerH + size) / 2);
        if (m_closeHover) {
            Gdiplus::SolidBrush hb(GColor(Line, 130));
            g.FillRectangle(&hb, m_closeRect.left, m_closeRect.top,
                            m_closeRect.Width(), m_closeRect.Height());
        }
        Gdiplus::Pen xp(GColor(m_closeHover ? Text : Muted));
        CRect c = m_closeRect;
        c.DeflateRect(size / 2 - 5, size / 2 - 5);
        g.DrawLine(&xp, REAL(c.left), REAL(c.top), REAL(c.right), REAL(c.bottom));
        g.DrawLine(&xp, REAL(c.right), REAL(c.top), REAL(c.left), REAL(c.bottom));
    }

    // —— 输入框自绘圆角边框（聚焦金色）——
    CUIntArray editIds;
    GetThemeEditIds(editIds);
    for (INT_PTR i = 0; i < editIds.GetSize(); ++i) {
        CWnd* edit = GetDlgItem(static_cast<UINT>(editIds[i]));
        if (!edit)
            continue;
        CRect fr;
        edit->GetWindowRect(&fr);
        ScreenToClient(&fr);
        fr.InflateRect(2, 2);
        mem.FillSolidRect(fr, EditBg);
        const bool focused = ::GetFocus() == edit->GetSafeHwnd();
        mem.FrameRect(&fr, CBrush::FromHandle(Solid(focused ? Accent : Line)));
    }

    dc.BitBlt(0, 0, rc.Width(), rc.Height(), &mem, 0, 0, SRCCOPY);
    mem.SelectObject(oldBm);
}

HBRUSH CThemeDialog::OnCtlColor(CDC* pDC, CWnd* pWnd, UINT nCtlColor)
{
    if (nCtlColor == CTLCOLOR_EDIT) {
        pDC->SetTextColor(Text);
        pDC->SetBkColor(EditBg);
        return Solid(EditBg);
    }
    if (nCtlColor == CTLCOLOR_STATIC) {
        pDC->SetTextColor(Muted);
        pDC->SetBkMode(TRANSPARENT);
        return Solid(Card);
    }
    return CDialog::OnCtlColor(pDC, pWnd, nCtlColor);
}

LRESULT CThemeDialog::OnNcHitTest(CPoint point)
{
    CRect rcClose = m_closeRect;
    ClientToScreen(&rcClose);
    if (rcClose.PtInRect(point))
        return HTCLIENT;   // 关闭钮不作为拖动热区
    CRect rcHdr;
    GetClientRect(&rcHdr);
    rcHdr.bottom = rcHdr.top + HeaderHeight(*this);
    ClientToScreen(&rcHdr);
    if (rcHdr.PtInRect(point))
        return HTCAPTION;   // 标题条拖动窗口
    return CDialog::OnNcHitTest(point);
}

void CThemeDialog::TrackHoverDialog()
{
    TRACKMOUSEEVENT tme = { sizeof(tme), TME_LEAVE, GetSafeHwnd(), 0 };
    ::TrackMouseEvent(&tme);
    m_tracked = true;
}

void CThemeDialog::OnMouseMove(UINT nFlags, CPoint point)
{
    const bool hover = m_closeRect.PtInRect(point) != FALSE;
    if (hover != m_closeHover) {
        m_closeHover = hover;
        InvalidateRect(m_closeRect, FALSE);
    }
    if (!m_tracked)
        TrackHoverDialog();
    CDialog::OnMouseMove(nFlags, point);
}

void CThemeDialog::OnMouseLeave()
{
    m_tracked = false;
    if (m_closeHover) {
        m_closeHover = false;
        InvalidateRect(m_closeRect, FALSE);
    }
}

void CThemeDialog::OnLButtonUp(UINT nFlags, CPoint point)
{
    if (m_closeRect.PtInRect(point))
        PostMessageW(WM_CLOSE);
    CDialog::OnLButtonUp(nFlags, point);
}

void CThemeDialog::OnClose()
{
    EndDialog(IDCANCEL);
}

void CThemeDialog::OnEditSetFocus(UINT nID)
{
    InvalidateEditFrame(nID);
}

void CThemeDialog::OnEditKillFocus(UINT nID)
{
    InvalidateEditFrame(nID);
}

void CThemeDialog::InvalidateEditFrame(UINT nID)
{
    CWnd* edit = GetDlgItem(nID);
    if (!edit)
        return;
    CRect fr;
    edit->GetWindowRect(&fr);
    ScreenToClient(&fr);
    fr.InflateRect(4, 4);   // 略大于绘制边框，覆盖描边变化区域
    InvalidateRect(fr, FALSE);
}
