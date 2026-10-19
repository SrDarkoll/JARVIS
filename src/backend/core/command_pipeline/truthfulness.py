"""Conservative checks for unsupported action reports in conversational replies."""

from __future__ import annotations

import re
import unicodedata

_EFFECT = (
    r"(?:abr\w*|abiert\w*|cerr\w*|minimiz\w*|maximiz\w*|ejecut\w*|guard\w*|redact\w*|"
    r"escrib\w*|reprodu\w*|anad\w*|agreg\w*|elimin\w*|borr\w*|envi\w*|"
    r"paus\w*|activ\w*|desactiv\w*|cambi\w*)"
)
_ACTION_REPORT = re.compile(
    rf"(?:^|[.!?\n]\s+)(?:yo\s+)?(?:ya\s+)?(?:"
    rf"(?:estoy\s+|he\s+|acabo de\s+|voy a\s+){_EFFECT}"
    rf"|(?:abriendo|cerrando|minimizando|ejecutando|guardando|reproduciendo)\b"
    rf"|(?:abri|minimice|guarde|anadi|agregue|envie|reproduje|pause|cerre)\b"
    r"|i(?: am|'m| have|'ve| will|'ll)\s+(?:now\s+)?"
    r"(?:open\w*|clos\w*|sav\w*|delet\w*|sent|send\w*|play\w*|paus\w*|add\w*|minimiz\w*|execut\w*)\b"
    r")",
    re.IGNORECASE,
)


def reports_unexecuted_action(text: str) -> bool:
    """Catch direct self-reports, not quoted examples or instructions to the user.

    This is a defensive language check, not a general semantic proof. Execution
    outcomes are separately restricted by the receipt contract.
    """
    normalized = "".join(
        char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char)
    ).replace("’", "'")
    return bool(_ACTION_REPORT.search(normalized.strip()))
