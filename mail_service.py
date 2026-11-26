"""Serviço de e-mail SkyPDV.

Envia e-mails transacionais via BlueSpark Cloud Webmail API
(POST /api/mail/webmail/send) usando apenas urllib — sem dependência de smtplib
ou da lib `requests`.

Configuração (.env):
    WEBMAIL_API_URL   — base da API, ex.: https://cloudmz.bluesparkmz.com
    WEBMAIL_API_KEY   — token bsml_... gerado no painel BlueSpark Cloud
    WEBMAIL_FROM_NAME — nome do remetente, ex.: "SkyPDV" (opcional)
    SKYPDV_PUBLIC_URL — URL pública da API SkyPDV para servir os logos (opcional)
"""

from __future__ import annotations

import html
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime
from string import Template

logger = logging.getLogger(__name__)


def _webmail_api_url() -> str:
    return os.getenv("WEBMAIL_API_URL", "https://cloudmz.bluesparkmz.com").strip().rstrip("/")


def _webmail_api_key() -> str:
    return os.getenv("WEBMAIL_API_KEY", "").strip()


def _webmail_from_name() -> str:
    return os.getenv("WEBMAIL_FROM_NAME", "SkyPDV").strip() or "SkyPDV"


def _skypdv_public_url() -> str:
    return os.getenv("SKYPDV_PUBLIC_URL", "https://skypdvmz.bluesparkmz.com").strip().rstrip("/")


def _logo_bluespark_url() -> str:
    return os.getenv(
        "LOGO_BLUESPARK_URL",
        f"{_skypdv_public_url()}/static/logos/bluespark_logo.png",
    ).strip()


def _logo_skypdv_url() -> str:
    return os.getenv(
        "LOGO_SKYPDV_URL",
        f"{_skypdv_public_url()}/static/logos/skypdv_logo.jpg",
    ).strip()


# ---------------------------------------------------------------------------
# Template HTML da fatura (compatível com Gmail, Outlook e Apple Mail)
# ---------------------------------------------------------------------------

INVOICE_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="pt">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Nova Fatura SkyPDV</title>
</head>
<body style="margin:0;padding:0;background-color:#f4f6fb;font-family:'Segoe UI',Arial,Helvetica,sans-serif;color:#1a1a2e;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f6fb;padding:28px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background-color:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 4px 28px rgba(79,47,232,0.12);">

          <!-- Cabeçalho com logos SkyPDV e BlueSpark MZ -->
          <tr>
            <td style="background:linear-gradient(135deg,#4f2fe8 0%,#2b1799 100%);background-color:#4f2fe8;padding:28px 32px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td valign="middle">
                    <table role="presentation" cellpadding="0" cellspacing="0">
                      <tr>
                        <td style="background-color:#ffffff;border-radius:10px;padding:6px 10px;">
                          <img src="$logo_skypdv" alt="SkyPDV" height="38" style="display:block;height:38px;width:auto;border:0;" />
                        </td>
                        <td style="padding:0 12px;">
                          <div style="width:1px;height:34px;background-color:rgba(255,255,255,0.35);"></div>
                        </td>
                        <td style="background-color:#ffffff;border-radius:10px;padding:6px 10px;">
                          <img src="$logo_bluespark" alt="BlueSpark MZ" height="38" style="display:block;height:38px;width:auto;border:0;" />
                        </td>
                      </tr>
                    </table>
                  </td>
                  <td align="right" valign="middle" style="color:#ffffff;">
                    <div style="font-size:20px;font-weight:700;line-height:1.2;">Nova Fatura</div>
                    <div style="font-size:12px;color:#e0d9ff;margin-top:3px;">Subscrição SkyPDV</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Corpo -->
          <tr>
            <td style="padding:32px;">
              <p style="margin:0 0 20px 0;font-size:16px;line-height:1.5;color:#1a1a2e;">
                Olá, <strong style="color:#4f2fe8;">$terminal_name</strong>!<br />
                Foi emitida uma nova fatura de subscrição para o teu terminal <strong>SkyPDV</strong>.
              </p>

              <!-- Card com detalhes da fatura -->
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f7f5ff;border:1.5px solid #d6ccff;border-radius:12px;margin-bottom:24px;">
                <tr>
                  <td style="padding:20px 24px;">
                    <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:#6d5bd0;margin-bottom:14px;">
                      Detalhes da Fatura
                    </div>
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:14px;">
                      <tr>
                        <td style="padding:8px 0;color:#6b6b8d;border-bottom:1px solid #e8e3ff;">Referência</td>
                        <td align="right" style="padding:8px 0;font-weight:600;color:#1a1a2e;border-bottom:1px solid #e8e3ff;">#$order_id</td>
                      </tr>
                      <tr>
                        <td style="padding:8px 0;color:#6b6b8d;border-bottom:1px solid #e8e3ff;">Terminal</td>
                        <td align="right" style="padding:8px 0;font-weight:600;color:#1a1a2e;border-bottom:1px solid #e8e3ff;">$terminal_name</td>
                      </tr>
                      <tr>
                        <td style="padding:8px 0;color:#6b6b8d;border-bottom:1px solid #e8e3ff;">Plano</td>
                        <td align="right" style="padding:8px 0;font-weight:600;color:#1a1a2e;border-bottom:1px solid #e8e3ff;">$plan</td>
                      </tr>
                      <tr>
                        <td style="padding:8px 0;color:#6b6b8d;border-bottom:1px solid #e8e3ff;">Período</td>
                        <td align="right" style="padding:8px 0;font-weight:600;color:#1a1a2e;border-bottom:1px solid #e8e3ff;">$months mês(es)</td>
                      </tr>
                      <tr>
                        <td style="padding:10px 0;color:#6b6b8d;border-bottom:1px solid #e8e3ff;">Valor a Pagar</td>
                        <td align="right" style="padding:10px 0;font-size:20px;font-weight:700;color:#4f2fe8;border-bottom:1px solid #e8e3ff;">$amount $currency</td>
                      </tr>
                      <tr>
                        <td style="padding:8px 0;color:#6b6b8d;border-bottom:1px solid #e8e3ff;">Estado</td>
                        <td align="right" style="padding:8px 0;border-bottom:1px solid #e8e3ff;">
                          <span style="display:inline-block;background-color:#fff4e6;color:#d97706;border:1px solid #fcd34d;border-radius:20px;padding:2px 10px;font-size:12px;font-weight:700;">
                            Por pagar
                          </span>
                        </td>
                      </tr>
                      <tr>
                        <td style="padding:8px 0;color:#6b6b8d;">Data de Emissão</td>
                        <td align="right" style="padding:8px 0;font-weight:600;color:#1a1a2e;">$created_at</td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>

              <!-- Botão CTA SkyWallet (se houver link) -->
              $payment_cta

              <!-- Instruções de transferência M-Pesa -->
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#fffbea;border:1.5px solid #fcd34d;border-radius:10px;margin-bottom:24px;">
                <tr>
                  <td style="padding:16px 20px;">
                    <div style="font-size:13px;font-weight:700;color:#92400e;margin-bottom:8px;">
                      Pagar via Transferência M-Pesa
                    </div>
                    <p style="margin:0;font-size:13px;color:#78350f;line-height:1.6;">
                      Transfere <strong>$amount $currency</strong> para um dos números abaixo e envia o comprovativo ao suporte SkyPDV:<br /><br />
                      <strong style="font-size:15px;letter-spacing:0.5px;">+258 860 716 912</strong> &nbsp;|&nbsp;
                      <strong style="font-size:15px;letter-spacing:0.5px;">+258 852 411 827</strong><br /><br />
                      Indica sempre a referência <strong>#$order_id</strong> na transferência.
                    </p>
                  </td>
                </tr>
              </table>

              <p style="margin:0;font-size:13px;color:#6b6b8d;line-height:1.6;">
                Após a confirmação do pagamento a tua subscrição será renovada automaticamente.<br />
                Precisas de ajuda? Contacta o suporte em
                <a href="mailto:suporte@bluesparkmz.com" style="color:#4f2fe8;text-decoration:none;">suporte@bluesparkmz.com</a>
                ou pelo WhatsApp <strong>+258 860 716 912</strong>.
              </p>
            </td>
          </tr>

          <!-- Rodapé -->
          <tr>
            <td align="center" style="background-color:#f4f6fb;padding:20px 32px;">
              <p style="margin:0;font-size:12px;color:#9ca3af;line-height:1.6;">
                Este e-mail foi enviado automaticamente pelo sistema SkyPDV.<br />
                &copy; BlueSpark MZ · Todos os direitos reservados.<br />
                <a href="https://skypdvmz.bluesparkmz.com" style="color:#4f2fe8;text-decoration:none;">skypdvmz.bluesparkmz.com</a>
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

INVOICE_TEXT_TEMPLATE = """\
SkyPDV — Nova Fatura de Subscrição
===================================

Olá, $terminal_name!

Foi emitida uma nova fatura para o teu terminal SkyPDV.

Detalhes:
  Referência : #$order_id
  Terminal   : $terminal_name
  Plano      : $plan
  Período    : $months mês(es)
  Valor      : $amount $currency
  Estado     : Por pagar
  Data       : $created_at

$payment_text\
Pagar via Transferência M-Pesa:
  +258 860 716 912  |  +258 852 411 827
  Indica a referência #$order_id.

Suporte: suporte@bluesparkmz.com | +258 860 716 912

BlueSpark MZ — https://bluesparkmz.com
"""


def _format_created_at(value: str | datetime | None) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    if isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return dt.strftime("%d/%m/%Y %H:%M")
        except ValueError:
            return value.strip()
    return datetime.utcnow().strftime("%d/%m/%Y %H:%M")


def _send_via_webmail(*, to: str, subject: str, body_html: str, body_text: str) -> bool:
    """Envia e-mail via BlueSpark Webmail API. Retorna True se bem-sucedido."""
    api_key = _webmail_api_key()
    if not api_key:
        logger.warning("WEBMAIL_API_KEY não definido — e-mail não enviado para %s", to)
        return False

    url = f"{_webmail_api_url()}/api/mail/webmail/send"
    payload = json.dumps(
        {
            "to": [to],
            "subject": subject,
            "body_html": body_html,
            "body_text": body_text,
            "from_name": _webmail_from_name(),
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-Key": api_key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            logger.info("E-mail enviado para %s — status %s", to, resp.status)
            return True
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except Exception:
            detail = exc.reason
        logger.error("Falha ao enviar e-mail para %s: HTTP %s — %s", to, exc.code, detail)
        return False
    except (urllib.error.URLError, TimeoutError) as exc:
        logger.error("Falha ao enviar e-mail para %s: %s", to, exc)
        return False


def send_invoice_created(
    *,
    to_email: str,
    terminal_name: str,
    order_id: str,
    plan: str,
    months: int,
    amount: float,
    currency: str = "MZN",
    payment_url: str | None = None,
    created_at: str | datetime | None = None,
) -> bool:
    """Notifica o dono do terminal sobre nova fatura de subscrição."""
    email = (to_email or "").strip()
    if not email:
        logger.warning("send_invoice_created: to_email vazio — e-mail não enviado")
        return False

    plan_label = (plan or "basic").strip().title()
    amount_str = f"{float(amount):,.2f}".replace(",", " ")
    created_label = _format_created_at(created_at)
    safe_url = (payment_url or "").strip()

    if safe_url:
        escaped_url = html.escape(safe_url, quote=True)
        payment_cta = (
            '<div style="text-align:center;margin-bottom:24px;">'
            f'<a href="{escaped_url}" target="_blank" '
            'style="display:inline-block;background-color:#4f2fe8;color:#ffffff;text-decoration:none;'
            'padding:14px 34px;border-radius:50px;font-size:15px;font-weight:700;">'
            "Pagar Online (SkyWallet)"
            "</a>"
            "</div>"
        )
        payment_text = f"Pagar online (SkyWallet):\n  {safe_url}\n\n"
    else:
        payment_cta = ""
        payment_text = ""

    html_vars = {
        "logo_skypdv": html.escape(_logo_skypdv_url(), quote=True),
        "logo_bluespark": html.escape(_logo_bluespark_url(), quote=True),
        "terminal_name": html.escape(terminal_name or "Terminal SkyPDV"),
        "order_id": html.escape(order_id),
        "plan": html.escape(plan_label),
        "months": str(months),
        "amount": html.escape(amount_str),
        "currency": html.escape(currency),
        "created_at": html.escape(created_label),
        "payment_cta": payment_cta,
    }
    text_vars = {
        "terminal_name": terminal_name or "Terminal SkyPDV",
        "order_id": order_id,
        "plan": plan_label,
        "months": str(months),
        "amount": amount_str,
        "currency": currency,
        "created_at": created_label,
        "payment_text": payment_text,
    }

    body_html = Template(INVOICE_HTML_TEMPLATE).safe_substitute(html_vars)
    body_text = Template(INVOICE_TEXT_TEMPLATE).safe_substitute(text_vars)
    subject = f"Nova Fatura SkyPDV — {plan_label} · {months} mês(es) · {amount_str} {currency}"

    return _send_via_webmail(to=email, subject=subject, body_html=body_html, body_text=body_text)
