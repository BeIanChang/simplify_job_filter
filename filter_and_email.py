import os
import os
import re
import html
import requests
from typing import List, Dict, Optional, Tuple

DEFAULT_ALLOW_LOCATIONS = [
    "Canada",
    "Remote (Canada)",
    "Remote (Canada/US)",
    "Remote (Canada/USA)",
    "Remote Canada",
    "Remote - Canada",
]

REPO_OWNER = "SimplifyJobs"
REPO_NAME = "Summer2026-Internships"


def fetch_readme(ref: str = "dev") -> str:
    """Fetch README.md at a branch or commit SHA."""
    url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{ref}/README.md"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def latest_two_shas(branch: str = "dev") -> List[str]:
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/commits"
    params = {"sha": branch, "per_page": 2}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    commits = resp.json()
    return [c["sha"] for c in commits][:2]


def extract_tables(readme: str) -> List[str]:
    return re.findall(r"<table>.*?</table>", readme, flags=re.S)


def parse_table(table_html: str) -> List[Dict[str, str]]:
    row_re = re.compile(r"<tr>(.*?)</tr>", re.S)
    cell_re = re.compile(r"<t[dh]>(.*?)</t[dh]>", re.S)
    rows = []
    for m in row_re.finditer(table_html):
        cell_values = []
        for raw_cell in cell_re.findall(m.group(1)):
            cleaned = re.sub(r"<br\s*/?>", " | ", raw_cell, flags=re.I)
            cleaned = html.unescape(re.sub(r"<[^>]+>", "", cleaned)).strip()
            cell_values.append(cleaned)
        if len(cell_values) == 5 and cell_values[0] != "Company":
            rows.append({
                "company": cell_values[0],
                "role": cell_values[1],
                "location": cell_values[2],
                "application": cell_values[3],
                "age": cell_values[4],
            })
    return rows


def filter_rows(rows: List[Dict[str, str]], allow_locations: List[str], include_keywords: Optional[List[str]] = None, exclude_keywords: Optional[List[str]] = None) -> List[Dict[str, str]]:
    def matches_location(loc: str) -> bool:
        norm = loc.lower()
        for allow in allow_locations:
            if allow.lower() in norm:
                return True
        return False

    def matches_keywords(text: str, keywords: List[str]) -> bool:
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in keywords)

    filtered = []
    for row in rows:
        if not matches_location(row.get("location", "")):
            continue
        title_text = f"{row.get('company','')} {row.get('role','')}"
        if include_keywords and not matches_keywords(title_text, include_keywords):
            continue
        if exclude_keywords and matches_keywords(title_text, exclude_keywords):
            continue
        filtered.append(row)
    return filtered


def unique_key(row: Dict[str, str]) -> Tuple[str, str, str]:
    return (
        row.get("company", "").strip(),
        row.get("role", "").strip(),
        row.get("location", "").strip(),
    )


def diff_new_rows(current: List[Dict[str, str]], previous: List[Dict[str, str]]) -> List[Dict[str, str]]:
    prev_keys = {unique_key(r) for r in previous}
    return [r for r in current if unique_key(r) not in prev_keys]


def format_plain(rows: List[Dict[str, str]]) -> str:
    if not rows:
        return "No new matching jobs today."
    lines = []
    for r in rows:
        company = r.get("company", "")
        role = r.get("role", "")
        location = r.get("location", "")
        app = r.get("application", "")
        link = ""
        url_match = re.search(r"https?://\S+", app)
        if url_match:
            link = url_match.group(0)
        line = f"{company} — {role} — {location}"
        if link:
            line += f" — {link}"
        lines.append(line)
    return "\n".join(lines)


def send_email(sendgrid_api_key: str, to_email: str, from_email: str, subject: str, body: str) -> None:
    url = "https://api.sendgrid.com/v3/mail/send"
    payload = {
        "personalizations": [
            {"to": [{"email": to_email}]}
        ],
        "from": {"email": from_email},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": body}
        ],
    }
    resp = requests.post(url, json=payload, headers={"Authorization": f"Bearer {sendgrid_api_key}"}, timeout=30)
    resp.raise_for_status()


def env_list(name: str) -> Optional[List[str]]:
    val = os.getenv(name)
    if not val:
        return None
    return [v.strip() for v in val.split(',') if v.strip()]


def main():
    sendgrid_key = os.getenv("SENDGRID_API_KEY")
    to_email = os.getenv("EMAIL_TO")
    from_email = os.getenv("EMAIL_FROM")
    if not sendgrid_key or not to_email or not from_email:
        raise SystemExit("Missing SENDGRID_API_KEY, EMAIL_TO, or EMAIL_FROM")

    branch = os.getenv("SOURCE_BRANCH", "dev")
    allow_locations = env_list("LOCATION_ALLOWLIST") or DEFAULT_ALLOW_LOCATIONS
    include_keywords = env_list("INCLUDE_KEYWORDS")
    exclude_keywords = env_list("EXCLUDE_KEYWORDS")

    shas = latest_two_shas(branch)
    current_ref = shas[0] if shas else branch
    previous_ref = shas[1] if len(shas) > 1 else None

    current_readme = fetch_readme(ref=current_ref)
    tables_current = extract_tables(current_readme)
    if not tables_current:
        raise SystemExit("No tables found in current README")
    current_rows = parse_table(tables_current[0])

    previous_rows: List[Dict[str, str]] = []
    if previous_ref:
        prev_readme = fetch_readme(ref=previous_ref)
        tables_prev = extract_tables(prev_readme)
        if tables_prev:
            previous_rows = parse_table(tables_prev[0])

    new_rows = diff_new_rows(current_rows, previous_rows)
    filtered = filter_rows(new_rows, allow_locations, include_keywords, exclude_keywords)
    body = format_plain(filtered)
    subject = f"Summer 2026 internships digest (new: {len(filtered)})"
    send_email(sendgrid_key, to_email, from_email, subject, body)


if __name__ == "__main__":
    main()
