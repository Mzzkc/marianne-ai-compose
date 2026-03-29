# mozart/cli/commands: Command modules for Mozart CLI.
#
# Each module in this package provides one or more CLI commands.

from .cancel import cancel
from .diagnose import diagnose, errors, history, logs
from .doctor import doctor
from .pause import modify, pause
from .recover import recover
from .resume import resume
from .run import run
from .status import clear, list_jobs, status
from .validate import validate

__all__ = [
    # cancel.py
    "cancel",
    # diagnose.py
    "logs",
    "errors",
    "diagnose",
    "history",
    # doctor.py
    "doctor",
    # pause.py
    "pause",
    "modify",
    # recover.py
    "recover",
    # resume.py
    "resume",
    # run.py
    "run",
    # status.py
    "status",
    "list_jobs",
    "clear",
    # validate.py
    "validate",
]
