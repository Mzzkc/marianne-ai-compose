#!/usr/bin/env python3
"""Check a Strudel file for sample names that will produce errors.

Catches the "sound X not found" problem BEFORE the browser sees it.
Returns error count and details. 0 = clean.

Usage: python3 check-strudel-errors.py <strudel-file>
"""

import re
import sys
from pathlib import Path

# Valid built-in drum machine banks
VALID_BANKS = {
    "RolandTR808", "RolandTR909", "RolandTR606", "RolandTR707",
    "RolandTR505", "RolandTR626", "RolandTR727",
    "LinnDrum", "Linn9000", "LinnLM1", "LinnLM2",
    "EmuDrumulator", "EmuSP12", "EmuModular",
    "KorgKPR77", "KorgDDM110", "KorgMinipops", "KorgM1",
    "AkaiMPC60", "AkaiLinn", "AkaiXR10",
    "AlesisHR16", "AlesisSR16",
    "BossDR110", "BossDR220", "BossDR55", "BossDR550", "BossDR660",
    "CasioRZ1", "CasioSK1", "CasioVL1",
    "OberheimDMX", "MFB512",
    "YamahaRX5", "YamahaRX21", "YamahaRY30", "YamahaTG33",
    "SimmonsSDS5", "SimmonsSDS400",
}

# Valid synth sounds (usable in s() or .sound())
VALID_SYNTHS = {
    "sawtooth", "sine", "square", "triangle", "white", "pink",
    "supersaw", "pulse",
}

# Valid built-in sample names (Strudel / Dirt-Samples)
VALID_SAMPLES = {
    "bd", "sd", "cp", "hh", "oh", "cr", "ride", "rim", "tom",
    "cb", "sh", "conga", "bongo", "tabla", "mt", "ht", "lt",
    "tb", "perc", "misc", "fx", "sn", "rd", "hc",
    # Common Strudel/Dirt-Samples names
    "arpy", "bass", "bass3", "chin", "circus", "click",
    "cosmicg", "crow", "d", "diphone", "diphone2", "dist",
    "dork2", "dr", "dr2", "dr55", "dr_few", "drumtraks",
    "e", "east", "electro1", "em2", "erk", "feel",
    "feelfx", "flick", "fm", "future", "gab", "gabba",
    "glasstap", "glitch", "glitch2", "gretsch", "gtr",
    "hand", "hardcore", "hardkick", "haw", "hoover",
    "house", "ht", "if", "ifdrums", "incoming", "industrial",
    "insect", "jazz", "jungbass", "jungle", "jvbass",
    "kicklinn", "koy", "kurt", "latibro", "led", "less",
    "lighter", "linnhats", "lt", "made", "made2", "mash",
    "mash2", "metal", "miniyeah", "monstra", "moog",
    "mouth", "mp3", "msg", "mt", "mute", "newnotes",
    "noise", "notes", "numbers", "oc", "odx", "off",
    "outdoor", "pad", "padlong", "pebbles", "perc",
    "peri", "pluck", "popkick", "print", "proc", "procshort",
    "psr", "rave", "rave2", "ravemono", "realclaps",
    "reverbkick", "rm", "rs", "sax", "sd", "seawolf",
    "sequential", "sf", "sheffield", "short", "sid",
    "sine", "sitar", "sn", "space", "speakspell",
    "speech", "speechless", "speedupdown", "stab", "stomp",
    "straight", "sundance", "tabla", "tabla2", "tablex",
    "tacscan", "tech", "techno", "tink", "tok", "toys",
    "trump", "ul", "ulgab", "uxay", "v", "voodoo",
    "wind", "wobble", "world", "xmas", "yeah",
}


def _load_manifest_names(strudel_path: Path) -> set[str]:
    """Load sample names from the track's strudel.json manifest, if it exists.

    The prep stage can download custom samples — their names are valid for
    this track even if they aren't in the built-in allowlist.
    """
    import json
    names: set[str] = set()
    # Walk up from the strudel file to find samples/strudel.json
    for parent in [strudel_path.parent.parent, strudel_path.parent.parent.parent]:
        manifest = parent / "samples" / "strudel.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text())
                for k in data:
                    if k != "_base":
                        names.add(k.lower())
            except (json.JSONDecodeError, TypeError):
                pass
            break
    return names


def check_file(path: Path) -> list[str]:
    """Check a strudel file for likely errors. Returns list of error messages."""
    content = path.read_text()
    errors = []

    # Build combined allowlist: built-in samples + synths + manifest samples
    manifest_names = _load_manifest_names(path)
    all_valid_sounds = VALID_SAMPLES | VALID_SYNTHS | manifest_names

    # Check for invented bank names
    bank_refs = re.findall(r'\.bank\(["\']([^"\']+)["\']\)', content)
    for bank in bank_refs:
        if bank not in VALID_BANKS:
            errors.append(f'INVALID_BANK: .bank("{bank}") — not a built-in drum machine')

    # Check s() calls for valid sample/synth names
    s_calls = re.findall(r's\(["\']([^"\']+)["\']\)', content)
    for pattern in s_calls:
        tokens = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*(?::\d+)?', pattern)
        for token in tokens:
            name = token.split(":")[0].lower()
            if name in ("x", "t", "e", "r", "~") or name.isdigit():
                continue
            if name not in all_valid_sounds and len(name) > 1:
                errors.append(f'UNKNOWN_SAMPLE: s("{name}") — not in built-in library or manifest')

    # Check .sound() calls for valid synth/sample names
    sound_calls = re.findall(r'\.sound\(["\']([^"\']+)["\']\)', content)
    for name in sound_calls:
        name_lower = name.lower()
        if name_lower not in all_valid_sounds:
            errors.append(f'UNKNOWN_SOUND: .sound("{name}") — not a valid synth or sample name')

    # Check for .bpm() which isn't valid Strudel
    if ".bpm(" in content:
        errors.append("INVALID_METHOD: .bpm() does not exist — use setcps(bpm/60/4)")

    # Check for wrong CPS formula
    if re.search(r'setcps\s*\(\s*\d+\s*/\s*120\s*\)', content):
        errors.append("WRONG_CPS: setcps(bpm/120) is wrong — use setcps(bpm/60/4)")

    # Check for note() without .sound() or .s() — silent failure
    if "note(" in content and ".sound(" not in content and 'sound("' not in content \
       and ".s(" not in content and '.s("' not in content:
        errors.append("SILENT_NOTE: note() without .sound() or .s() — synth patterns need .sound('sawtooth') or similar")

    # Check for Hydra pattern-order violation — Hydra line without semicolon as
    # last expression means audio is silent (scheduler gets a Hydra object, not a Pattern).
    if "initHydra" in content:
        lines = [l.strip() for l in content.strip().split("\n") if l.strip() and not l.strip().startswith("//")]
        if lines:
            last = lines[-1]
            hydra_endings = [".out()", ".out();"]
            if any(last.endswith(e.rstrip(";")) for e in hydra_endings) and not last.endswith(";"):
                errors.append("HYDRA_ORDER: last expression is a Hydra line without semicolon — audio will be silent. stack() must be last.")

    return errors


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 check-strudel-errors.py <strudel-file>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"0 errors (file not found: {path})")
        sys.exit(0)

    errors = check_file(path)

    if errors:
        print(f"{len(errors)} errors:")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)
    else:
        print("0 errors")
        sys.exit(0)


if __name__ == "__main__":
    main()
