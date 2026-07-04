"""
MonitoringService: Management of heartbeats and system health.
Refactored to use APScheduler (Non-Blocking).
"""

import threading
import time as _time
from datetime import datetime

import psutil
from core.jarvis_config import BRIEFING_HORA, HEARTBEAT_INTERVALO
from core.jarvis_observability import obs_event
from core.jarvis_state import heartbeat_state
from core.service_container import services

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    SCHEDULER_AVAILABLE = True
except ImportError:
    BackgroundScheduler = None
    CronTrigger = None
    SCHEDULER_AVAILABLE = False


class MonitoringService:
    def __init__(self):
        self._telegram_manager = None
        self._brain_state = None
        self._security_manager = None
        self._ejecutar_briefing_func = None
        self._check_briefing_sent_func = None

        self._scheduler = BackgroundScheduler(daemon=True) if SCHEDULER_AVAILABLE else None

    def inject_dependencies(
        self,
        telegram_manager,
        brain_state,
        security_manager,
        ejecutar_briefing_func,
        check_briefing_sent_func,
    ):
        self._telegram_manager = telegram_manager
        self._brain_state = brain_state
        self._security_manager = security_manager
        self._ejecutar_briefing_func = ejecutar_briefing_func
        self._check_briefing_sent_func = check_briefing_sent_func

    def _heartbeat_proactive_healthcheck(self):
        """Scheduled task for system health check."""
        try:
            # Obtención instantánea (interval=None) para no bloquear el scheduler
            cpu = float(psutil.cpu_percent(interval=None))
            ram = float(psutil.virtual_memory().percent)

            if self._security_manager:
                with self._security_manager.PROACTIVE_LOCK:
                    self._security_manager.PROACTIVE_STATE["last_health_check"] = (
                        datetime.now().isoformat(timespec="seconds")
                    )

            heartbeat_state["cpu_high_streak"] = (
                int(heartbeat_state.get("cpu_high_streak", 0)) + 1 if cpu >= 85.0 else 0
            )
            heartbeat_state["ram_high_streak"] = (
                int(heartbeat_state.get("ram_high_streak", 0)) + 1 if ram >= 90.0 else 0
            )

            if heartbeat_state["cpu_high_streak"] >= 2:
                if self._security_manager:
                    self._security_manager._proactive_push_alert(
                        "cpu_high",
                        f"Critical CPU usage ({cpu:.0f}%).",
                        severity="warning",
                        key="cpu_high_streak",
                        send_telegram=True,
                        cooldown=1800,
                    )

            if heartbeat_state["ram_high_streak"] >= 2:
                if self._security_manager:
                    self._security_manager._proactive_push_alert(
                        "ram_high",
                        f"Critical RAM usage ({ram:.0f}%).",
                        severity="warning",
                        key="ram_high_streak",
                        send_telegram=True,
                        cooldown=1800,
                    )

            # Check de errores en plugins
            plugin_errors = (
                self._brain_state.PLUGIN_STATE.get("errors") if self._brain_state else {}
            )
            if plugin_errors and heartbeat_state.get(
                "last_plugin_error_alert"
            ) != datetime.now().strftime("%Y-%m-%d"):
                heartbeat_state["last_plugin_error_alert"] = datetime.now().strftime("%Y-%m-%d")
                if self._security_manager:
                    self._security_manager._proactive_push_alert(
                        "plugin_errors",
                        "Administrator, there are plugins with errors when loading. Check the plugin panel to correct them.",
                        severity="warning",
                        key="plugin_errors_daily",
                        send_telegram=False,
                        cooldown=3600,
                    )
        except Exception as e:
            obs_event("healthcheck_scheduler_error", error=str(e)[:200])

    def _check_reminders_task(self):
        """Scheduled task to check pending reminders."""
        ahora = datetime.now()
        reminders = services.get_reminders()
        pendientes = []
        try:
            for r in reminders:
                if ahora >= r["cuando"]:
                    msg = f"Administrator, reminder: {r['texto']}."
                    if self._telegram_manager:
                        # Lanzar envío en hilo independiente para no bloquear el scheduler
                        threading.Thread(
                            target=self._telegram_manager.send_message_sync,
                            args=(msg,),
                            daemon=True,
                        ).start()
                else:
                    pendientes.append(r)

            if len(pendientes) != len(reminders):
                from core import jarvis_state

                with jarvis_state.recordatorios_lock:
                    jarvis_state._recordatorios[:] = pendientes
        except Exception as e:
            print(f"[SCHEDULER] Error in reminders: {e}")

    def _daily_briefing_task(self):
        """Scheduled task for daily briefing (Cron)."""
        if self._ejecutar_briefing_func:
            from utils.jarvis_i18n import get_bt
            bt = get_bt()
            print(bt["log_morning_briefing"])
            threading.Thread(
                target=lambda: self._ejecutar_briefing_func("cron_scheduler"), daemon=True
            ).start()

    def _update_weather_task(self):
        """Updates the weather every 30 minutes."""
        try:
            from tools.utilities import _obtener_clima_logic

            desc, temp = _obtener_clima_logic()
            wc = services.weather_cache
            wc["temp"] = temp
            wc["desc"] = desc
            wc["last_update"] = _time.time()
            from utils.jarvis_i18n import get_bt
            bt = get_bt()
            print(bt["log_weather_updated"].format(temp=temp, desc=desc))
        except Exception as e:
            print(f"[SCHEDULER] Failed to update weather: {e}")

    def start_heartbeat(self, ip_cleanup_func=None):
        """Starts all scheduled tasks with APScheduler."""
        if not SCHEDULER_AVAILABLE or self._scheduler is None:
            print("[SCHEDULER] APScheduler is not installed; background monitoring is disabled.")
            return False

        # 1. Healthcheck (Heartbeats)
        self._scheduler.add_job(
            self._heartbeat_proactive_healthcheck,
            "interval",
            seconds=HEARTBEAT_INTERVALO,
            id="healthcheck",
        )

        # 2. Reminders (Every 20 seconds)
        self._scheduler.add_job(self._check_reminders_task, "interval", seconds=20, id="reminders")

        # 3. Daily Briefing (Cron)
        # Se programa para que se ejecute una vez al día a la hora definida
        self._scheduler.add_job(
            self._daily_briefing_task,
            CronTrigger(hour=BRIEFING_HORA, minute=0),
            id="daily_briefing",
        )

        # 4. IP Cleanup (Every hour)
        if ip_cleanup_func:
            self._scheduler.add_job(ip_cleanup_func, "interval", hours=1, id="ip_cleanup")

        # 5. Weather (Every 30 minutes)
        self._scheduler.add_job(
            self._update_weather_task, "interval", minutes=30, id="weather_update"
        )
        # Lanzar primer update inmediato
        self._scheduler.add_job(self._update_weather_task, id="weather_init")

        self._scheduler.start()
        from utils.jarvis_i18n import get_bt
        bt = get_bt()
        print(bt["log_scheduler_active"].format(count=len(self._scheduler.get_jobs())))
        return True


monitoring_service = MonitoringService()
