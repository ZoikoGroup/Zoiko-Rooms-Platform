"""ZR-COMMS-EMAIL-001 Section 04 -- email design system.

- Single-column, 600 px max content width, mobile-first (Section 4.1).
- Brand header: Zoiko navy #12243F, Rooms teal #0B9C90, purple accent #5B47F5.
- Body text 16 px, line height 1.55, left aligned; primary CTA at least
  44 x 44 px with a descriptive label.
- Preheader, dark-mode support, and a complete plain-text version of every
  message carrying the same decision, deadline, amount and CTA meaning.
- No images carry meaning (the wordmark is live text), no tracking pixels,
  no third-party content (Section 3.3).

Every dynamic value is HTML-escaped here; builders pass plain strings."""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

from app.services.email import blocks as B
from app.services.email.registry import TIER_3, TemplateSpec

NAVY = "#12243F"
TEAL = "#0B9C90"
PURPLE = "#5B47F5"
INK = "#1F2937"
MUTED = "#5B6472"
CANVAS = "#F3F5F9"
PANEL = "#F7F9FC"
RULE = "#E3E8EF"
WARNING = "#B45309"

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
_TONE_COLOR = {"teal": TEAL, "warning": WARNING, "neutral": MUTED}


@dataclass
class Message:
    spec: TemplateSpec
    subject_vars: dict = field(default_factory=dict)
    heading: str = ""
    first_name: str = ""
    intro: list[str] = field(default_factory=list)
    facts: list[tuple[str, str]] = field(default_factory=list)
    items: list[str] = field(default_factory=list)
    code: str = ""  # one-time code, shown large and never in subject/preheader
    outro: list[str] = field(default_factory=list)
    cta_url: str = ""
    unsubscribe_url: str = ""
    variant: str = ""
    # Section 1.3: same key = same message; the second send is suppressed.
    # deliver() appends the recipient, so one key can cover both parties.
    dedupe_key: str = ""
    related_entity_type: str = ""
    related_entity_id: str = ""
    # A variant whose meaning differs from the template's default subject
    # (e.g. AGR-002 "all signers completed" is not "Action required").
    subject_override: str = ""

    @property
    def subject(self) -> str:
        if self.subject_override:
            return self.subject_override
        return self.spec.subject.format(**self.subject_vars)

    @property
    def preheader(self) -> str:
        return self.spec.preheader.format(**self.subject_vars)

    @property
    def footer_blocks(self) -> list[B.Block]:
        chosen = [B.BLOCKS[k] for k in self.spec.blocks]
        chosen.append(B.MARKETING if self.spec.tier == TIER_3 else B.SERVICE)
        return chosen


def greeting(first_name: str) -> str:
    return f"Hi {first_name}," if first_name else "Hi,"


# ------------------------------------------------------------------ plain text

def render_text(m: Message) -> str:
    lines = ["ZOIKO ROOMS", "", m.heading or m.subject, "", greeting(m.first_name), ""]
    for p in m.intro:
        lines += [p, ""]
    if m.code:
        lines += [f"Your code: {m.code}", ""]
    if m.facts:
        lines += [f"{label}: {value}" for label, value in m.facts] + [""]
    if m.items:
        lines += [f"- {item}" for item in m.items] + [""]
    for p in m.outro:
        lines += [p, ""]
    if m.cta_url and m.spec.cta_label:
        lines += [f"{m.spec.cta_label}: {m.cta_url}", ""]
    if m.unsubscribe_url:
        lines += [f"Unsubscribe: {m.unsubscribe_url}", ""]
    lines.append("-" * 60)
    for block in m.footer_blocks:
        lines += ([block.title.upper()] if block.show_title else []) + [block.text, ""]
    lines.append(B.CORPORATE_IDENTITY)
    return "\n".join(lines).strip() + "\n"


# ------------------------------------------------------------------------ HTML

def _p(text: str) -> str:
    return (f'<p class="zr-text" style="margin:0 0 16px;font-family:{FONT};font-size:16px;line-height:1.55;'
            f'color:{INK};text-align:left;">{escape(text)}</p>')


def _facts(rows: list[tuple[str, str]]) -> str:
    cells = "".join(
        f'<tr><td class="zr-muted" style="padding:10px 14px;font-family:{FONT};font-size:14px;line-height:1.4;'
        f'color:{MUTED};width:42%;vertical-align:top;border-top:1px solid {RULE};">{escape(label)}</td>'
        f'<td class="zr-text" style="padding:10px 14px;font-family:{FONT};font-size:16px;line-height:1.45;'
        f'color:{INK};font-weight:600;vertical-align:top;border-top:1px solid {RULE};">{escape(value)}</td></tr>'
        for label, value in rows
    )
    return (f'<table role="presentation" class="zr-panel" width="100%" cellpadding="0" cellspacing="0" '
            f'style="margin:4px 0 20px;background:{PANEL};border:1px solid {RULE};border-radius:8px;'
            f'border-collapse:separate;">{cells}</table>')


def _items(items: list[str]) -> str:
    lis = "".join(f'<li style="margin:0 0 6px;">{escape(i)}</li>' for i in items)
    return (f'<ul class="zr-text" style="margin:0 0 18px;padding-left:22px;font-family:{FONT};font-size:16px;'
            f'line-height:1.55;color:{INK};">{lis}</ul>')


def _code(code: str) -> str:
    return (f'<p style="margin:4px 0 20px;"><span class="zr-panel" style="display:inline-block;padding:12px 20px;'
            f'background:{PANEL};border:1px solid {RULE};border-radius:8px;font-family:Consolas,Menlo,monospace;'
            f'font-size:28px;font-weight:700;letter-spacing:6px;color:{NAVY};" aria-label="One-time code">'
            f"{escape(code)}</span></p>")


def _cta(label: str, url: str) -> str:
    # Bulletproof button: the <a> itself is the 44 px target (Section 4.1).
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 24px;"><tr>'
            f'<td style="border-radius:8px;background:{NAVY};">'
            f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener" '
            f'style="display:inline-block;min-width:44px;padding:13px 26px;font-family:{FONT};font-size:16px;'
            f'line-height:18px;font-weight:700;color:#FFFFFF;text-decoration:none;border-radius:8px;">'
            f"{escape(label)}</a></td></tr></table>")


def _block(block: B.Block) -> str:
    color = _TONE_COLOR[block.tone]
    title = (
        f'<p style="margin:0 0 4px;font-family:{FONT};font-size:12px;letter-spacing:0.6px;font-weight:700;'
        f'text-transform:uppercase;color:{color};">{escape(block.title)}</p>'
    ) if block.show_title else ""
    return (f'<tr><td class="zr-panel" style="padding:12px 16px;background:{PANEL};border-left:4px solid {color};">'
            f'{title}'
            f'<p class="zr-muted" style="margin:0;font-family:{FONT};font-size:14px;line-height:1.5;color:{MUTED};">'
            f"{escape(block.text)}</p></td></tr><tr><td style=\"height:10px;line-height:10px;\">&nbsp;</td></tr>")


def render_html(m: Message) -> str:
    body = [f'<h1 class="zr-heading" style="margin:0 0 20px;font-family:{FONT};font-size:24px;line-height:1.3;'
            f'font-weight:700;color:{NAVY};">{escape(m.heading or m.subject)}</h1>', _p(greeting(m.first_name))]
    body += [_p(p) for p in m.intro]
    if m.code:
        body.append(_code(m.code))
    if m.facts:
        body.append(_facts(m.facts))
    if m.items:
        body.append(_items(m.items))
    body += [_p(p) for p in m.outro]
    if m.cta_url and m.spec.cta_label:
        body.append(_cta(m.spec.cta_label, m.cta_url))
    if m.unsubscribe_url:
        body.append(
            f'<p class="zr-muted" style="margin:0 0 8px;font-family:{FONT};font-size:14px;color:{MUTED};">'
            f'<a href="{escape(m.unsubscribe_url, quote=True)}" style="color:{TEAL};">Unsubscribe from this alert</a></p>'
        )
    footer = "".join(_block(b) for b in m.footer_blocks)
    # Invisible padding after the preheader stops clients pulling body text into the preview.
    preheader_pad = "&#8199;&#65279;&#847;" * 40

    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{escape(m.subject)}</title>
<style>
  @media (prefers-color-scheme: dark) {{
    .zr-canvas {{ background:#0B1220 !important; }}
    .zr-card {{ background:#111A2C !important; }}
    .zr-panel {{ background:#17233A !important; border-color:#24324D !important; }}
    .zr-text {{ color:#E6EAF0 !important; }}
    .zr-muted {{ color:#A9B3C1 !important; }}
    .zr-heading {{ color:#FFFFFF !important; }}
  }}
  @media only screen and (max-width:620px) {{
    .zr-pad {{ padding:24px 20px !important; }}
  }}
</style>
</head>
<body class="zr-canvas" style="margin:0;padding:0;background:{CANVAS};-webkit-text-size-adjust:100%;">
<div style="display:none;max-height:0;max-width:0;overflow:hidden;opacity:0;mso-hide:all;">{escape(m.preheader)}{preheader_pad}</div>
<table role="presentation" class="zr-canvas" width="100%" cellpadding="0" cellspacing="0" style="background:{CANVAS};">
<tr><td align="center" style="padding:24px 12px;">
  <table role="presentation" class="zr-card" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#FFFFFF;border-radius:12px;overflow:hidden;">
    <tr><td style="background:{NAVY};padding:20px 28px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td style="font-family:{FONT};font-size:20px;font-weight:800;letter-spacing:0.5px;color:#FFFFFF;">ZOIKO <span style="color:{TEAL};">ROOMS</span></td>
        <td align="right" style="font-family:{FONT};font-size:12px;font-weight:600;color:#C9D3E3;">{escape(m.spec.stream.label)}</td>
      </tr></table>
    </td></tr>
    <tr><td style="height:4px;line-height:4px;background:{PURPLE};">&nbsp;</td></tr>
    <tr><td class="zr-pad" style="padding:32px 28px 8px;">
      {"".join(body)}
    </td></tr>
    <tr><td class="zr-pad" style="padding:8px 28px 8px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{footer}</table>
    </td></tr>
    <tr><td class="zr-pad" style="padding:8px 28px 28px;">
      <p class="zr-muted" style="margin:0;font-family:{FONT};font-size:12px;line-height:1.5;color:{MUTED};">{escape(B.CORPORATE_IDENTITY)}</p>
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>"""
