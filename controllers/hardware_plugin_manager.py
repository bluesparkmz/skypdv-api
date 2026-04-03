from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


try:
    import win32print  # type: ignore
except ImportError:  # pragma: no cover
    win32print = None

try:
    import serial  # type: ignore
    from serial.tools import list_ports  # type: ignore
except ImportError:  # pragma: no cover
    serial = None
    list_ports = None


CASH_DRAWER_PULSE = b"\x1b\x70\x00\x19\xfa"


@dataclass
class HardwareResult:
    success: bool
    message: Optional[str] = None
    error: Optional[str] = None
    payload: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "success": self.success,
            "message": self.message,
            "error": self.error,
        }
        if self.payload:
            data.update(self.payload)
        return data


class HardwarePluginManager:
    def __init__(self) -> None:
        self.selected_printer: Optional[str] = None

    def list_printers(self) -> HardwareResult:
        if win32print is None:
            return HardwareResult(success=False, error="pywin32 is not installed", payload={"printers": []})

        printers: list[dict[str, Any]] = []
        default_printer = None
        try:
            default_printer = win32print.GetDefaultPrinter()
        except Exception:
            default_printer = None

        for flags, description, name, comment in win32print.EnumPrinters(
            win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        ):
            printers.append(
                {
                    "name": name,
                    "description": description,
                    "comment": comment,
                    "default": name == default_printer,
                }
            )

        return HardwareResult(success=True, message="Printers listed", payload={"printers": printers})

    def set_printer(self, printer_name: str) -> HardwareResult:
        if win32print is None:
            return HardwareResult(success=False, error="pywin32 is not installed")

        available = self.list_printers().payload or {}
        printer_names = {printer["name"] for printer in available.get("printers", [])}
        if printer_name not in printer_names:
            return HardwareResult(success=False, error=f"Printer '{printer_name}' not found")

        self.selected_printer = printer_name
        return HardwareResult(success=True, message=f"Printer set to {printer_name}")

    def print_receipt(self, content: str, printer_name: Optional[str] = None) -> HardwareResult:
        if win32print is None:
            return HardwareResult(success=False, error="pywin32 is not installed")

        target_printer = printer_name or self.selected_printer
        if not target_printer:
            try:
                target_printer = win32print.GetDefaultPrinter()
            except Exception:
                target_printer = None

        if not target_printer:
            return HardwareResult(success=False, error="No printer selected")

        printer_handle = None
        try:
            raw_data = content.replace("\n", "\r\n").encode("utf-8", errors="replace")
            printer_handle = win32print.OpenPrinter(target_printer)
            job = win32print.StartDocPrinter(printer_handle, 1, ("SkyPDV Receipt", None, "RAW"))
            try:
                win32print.StartPagePrinter(printer_handle)
                win32print.WritePrinter(printer_handle, raw_data)
                win32print.EndPagePrinter(printer_handle)
            finally:
                win32print.EndDocPrinter(printer_handle)

            return HardwareResult(
                success=True,
                message=f"Receipt sent to printer {target_printer}",
                payload={"printer_name": target_printer, "job_id": job},
            )
        except Exception as exc:
            return HardwareResult(success=False, error=str(exc))
        finally:
            if printer_handle is not None:
                try:
                    win32print.ClosePrinter(printer_handle)
                except Exception:
                    pass

    def list_serial_ports(self) -> HardwareResult:
        if list_ports is None:
            return HardwareResult(success=False, error="pyserial is not installed", payload={"ports": []})

        ports = [
            {
                "device": port.device,
                "name": port.name,
                "description": port.description,
                "hwid": port.hwid,
            }
            for port in list_ports.comports()
        ]
        return HardwareResult(success=True, message="Ports listed", payload={"ports": ports})

    def open_cash_drawer(self, port: Optional[str] = None) -> HardwareResult:
        if port:
            return self._open_drawer_serial(port)
        return self._open_drawer_printer()

    def _open_drawer_serial(self, port: str) -> HardwareResult:
        if serial is None:
            return HardwareResult(success=False, error="pyserial is not installed")

        connection = None
        try:
            connection = serial.Serial(port=port, baudrate=9600, timeout=2)
            connection.write(CASH_DRAWER_PULSE)
            connection.flush()
            return HardwareResult(success=True, message=f"Cash drawer pulse sent to {port}")
        except Exception as exc:
            return HardwareResult(success=False, error=str(exc))
        finally:
            if connection is not None and connection.is_open:
                connection.close()

    def _open_drawer_printer(self) -> HardwareResult:
        if win32print is None:
            return HardwareResult(success=False, error="pywin32 is not installed")

        target_printer = self.selected_printer
        if not target_printer:
            try:
                target_printer = win32print.GetDefaultPrinter()
            except Exception:
                target_printer = None

        if not target_printer:
            return HardwareResult(success=False, error="No printer selected for cash drawer")

        printer_handle = None
        try:
            printer_handle = win32print.OpenPrinter(target_printer)
            win32print.StartDocPrinter(printer_handle, 1, ("SkyPDV Cash Drawer", None, "RAW"))
            try:
                win32print.StartPagePrinter(printer_handle)
                win32print.WritePrinter(printer_handle, CASH_DRAWER_PULSE)
                win32print.EndPagePrinter(printer_handle)
            finally:
                win32print.EndDocPrinter(printer_handle)
            return HardwareResult(success=True, message=f"Cash drawer opened via printer {target_printer}")
        except Exception as exc:
            return HardwareResult(success=False, error=str(exc))
        finally:
            if printer_handle is not None:
                try:
                    win32print.ClosePrinter(printer_handle)
                except Exception:
                    pass

