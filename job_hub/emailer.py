from __future__ import annotations

import html
import smtplib
from email.message import EmailMessage
from typing import Any

from job_hub.config import Settings


class DeliveryError(RuntimeError):
    pass


class Mailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send_daily_report(self, report: dict[str, Any]) -> str:
        if not self.settings.mail_enabled:
            return "skipped"
        self._validate()
        report_url = (
            f"{self.settings.base_url}/daily/{report['report_date']}"
        )
        stats = report["stats"]
        subject = (
            f"就业日报 {report['report_date']} | 新增 {stats['new']} 条，"
            f"7 日内截止 {stats['deadline_soon']} 条"
        )
        text = (
            f"就业日报已生成。\n\n"
            f"新增：{stats['new']} 条\n"
            f"更新：{stats['updated']} 条\n"
            f"7 日内截止：{stats['deadline_soon']} 条\n"
            f"在招总数：{stats['open_total']} 条\n\n"
            f"查看完整日报：{report_url}\n"
        )
        content = (
            "<html><body style='font-family:Arial,\"Microsoft YaHei\",sans-serif;"
            "color:#1d2939;line-height:1.65'>"
            f"<h2>就业日报 · {html.escape(report['report_date'])}</h2>"
            "<table style='border-collapse:collapse'>"
            f"<tr><td style='padding:4px 18px 4px 0'>今日新增</td><td><strong>{stats['new']}</strong> 条</td></tr>"
            f"<tr><td style='padding:4px 18px 4px 0'>信息更新</td><td><strong>{stats['updated']}</strong> 条</td></tr>"
            f"<tr><td style='padding:4px 18px 4px 0'>7 日内截止</td><td><strong>{stats['deadline_soon']}</strong> 条</td></tr>"
            f"<tr><td style='padding:4px 18px 4px 0'>当前在招</td><td><strong>{stats['open_total']}</strong> 条</td></tr>"
            "</table>"
            f"<p><a href='{html.escape(report_url, quote=True)}'>打开完整就业日报</a></p>"
            "</body></html>"
        )
        self._send(subject, text, content)
        return "sent"

    def send_failure_alert(self, subject: str, details: str) -> str:
        if not self.settings.mail_enabled:
            return "skipped"
        self._validate()
        self._send(
            f"[就业信息站异常] {subject}",
            details,
            (
                "<html><body style='font-family:Arial,\"Microsoft YaHei\",sans-serif'>"
                f"<h2>{html.escape(subject)}</h2><pre>{html.escape(details)}</pre>"
                "</body></html>"
            ),
        )
        return "sent"

    def _validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("SMTP_HOST", self.settings.smtp_host),
                ("SMTP_FROM", self.settings.smtp_from),
                ("SMTP_TO", self.settings.smtp_to),
            )
            if not value
        ]
        if missing:
            raise DeliveryError("Missing mail configuration: " + ", ".join(missing))

    def _send(self, subject: str, text: str, content: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.smtp_from
        message["To"] = ", ".join(self.settings.smtp_to)
        message.set_content(text)
        message.add_alternative(content, subtype="html")
        try:
            if self.settings.smtp_use_ssl:
                with smtplib.SMTP_SSL(
                    self.settings.smtp_host,
                    self.settings.smtp_port,
                    timeout=30,
                ) as client:
                    if self.settings.smtp_username:
                        client.login(
                            self.settings.smtp_username,
                            self.settings.smtp_password,
                        )
                    client.send_message(message)
            else:
                with smtplib.SMTP(
                    self.settings.smtp_host,
                    self.settings.smtp_port,
                    timeout=30,
                ) as client:
                    client.starttls()
                    if self.settings.smtp_username:
                        client.login(
                            self.settings.smtp_username,
                            self.settings.smtp_password,
                        )
                    client.send_message(message)
        except (OSError, smtplib.SMTPException) as error:
            raise DeliveryError(f"Email delivery failed: {error}") from error
