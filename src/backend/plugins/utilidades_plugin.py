from datetime import datetime


def get_tools(context):
    tool = context["tool"]

    @tool
    def hora_actual_plugin() -> str:
        """Devuelve la hora local actual en formato HH:MM."""
        return f"Hora actual: {datetime.now().strftime('%H:%M')}."

    @tool
    def fecha_actual_plugin() -> str:
        """Devuelve la fecha local actual en formato DD/MM/YYYY."""
        return f"Fecha actual: {datetime.now().strftime('%d/%m/%Y')}."

    return [hora_actual_plugin, fecha_actual_plugin]


