import os
import os
import re
import html
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
import requests
from typing import List, Dict, Optional, Tuple

DEFAULT_ALLOW_LOCATIONS: List[str] = []

REPO_OWNER = "SimplifyJobs"
REPO_NAME = "Summer2026-Internships"


def fetch_readme(ref: str = "dev") -> str:
    """Fetch README.md at a branch or commit SHA."""
    url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{ref}/README.md"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def latest_shas(branch: str = "dev", limit: int = 2) -> List[str]:
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/commits"
    params = {"sha": branch, "per_page": limit}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    commits = resp.json()
    return [c["sha"] for c in commits][:limit]


def extract_tables(readme: str) -> List[str]:
    return re.findall(r"<table>.*?</table>", readme, flags=re.S)


def parse_table(table_html: str) -> List[Dict[str, str]]:
    row_re = re.compile(r"<tr>(.*?)</tr>", re.S)
    cell_re = re.compile(r"<t[dh]>(.*?)</t[dh]>", re.S)
    rows = []
    for m in row_re.finditer(table_html):
        cell_values = []
        cell_links = []
        for raw_cell in cell_re.findall(m.group(1)):
            link_matches = re.findall(r"href=\"(https?://[^\"]+)\"", raw_cell)
            simplify_link = next((link for link in link_matches if "simplify.jobs" in link), "")
            cell_links.append(simplify_link or (link_matches[0] if link_matches else ""))
            cleaned = re.sub(r"<br\s*/?>", " | ", raw_cell, flags=re.I)
            cleaned = html.unescape(re.sub(r"<[^>]+>", "", cleaned)).strip()
            cell_values.append(cleaned)
        if len(cell_values) == 5 and cell_values[0] != "Company":
            rows.append({
                "company": cell_values[0],
                "role": cell_values[1],
                "location": cell_values[2],
                "application": cell_values[3],
                "application_url": cell_links[3] if len(cell_links) > 3 else "",
                "age": cell_values[4],
            })
    return rows


def parse_tables(readme: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for table_html in extract_tables(readme):
        rows.extend(parse_table(table_html))
    return rows


CANADA_TOKENS = [
    "canada",
    "ontario",
    "british columbia",
    "alberta",
    "manitoba",
    "saskatchewan",
    "quebec",
    "nova scotia",
    "new brunswick",
    "newfoundland",
    "labrador",
    "prince edward island",
    "pei",
    "northwest territories",
    "yukon",
    "nunavut",
]

CANADA_ABBREVIATIONS = [
    "ON",
    "BC",
    "AB",
    "MB",
    "SK",
    "QC",
    "NS",
    "NB",
    "NL",
    "PE",
    "NT",
    "YT",
    "NU",
]


def is_canada_location(location: str) -> bool:
    lower = location.lower()
    if any(token in lower for token in CANADA_TOKENS):
        return True
    for abbr in CANADA_ABBREVIATIONS:
        if re.search(rf"\b{abbr}\b", location):
            return True
    return False


def filter_rows(
    rows: List[Dict[str, str]],
    allow_locations: Optional[List[str]],
    include_keywords: Optional[List[str]] = None,
    exclude_keywords: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    def matches_location(loc: str) -> bool:
        if not allow_locations:
            return is_canada_location(loc)
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


def count_locations(rows: List[Dict[str, str]]) -> Tuple[int, int, int]:
    total = len(rows)
    canada = sum(1 for r in rows if is_canada_location(r.get("location", "")))
    other = total - canada
    return total, canada, other


def load_last_sha(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read().strip() or None


def save_last_sha(path: str, sha: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(sha)


def get_commits_since_time(branch: str, since_iso: str, limit: int = 50) -> List[str]:
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/commits"
    params = {"sha": branch, "per_page": limit, "since": since_iso}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    commits = resp.json()
    return [c["sha"] for c in commits]


def format_plain(
    rows: List[Dict[str, str]],
    total_new: int,
    canada_new: int,
    other_new: int,
) -> str:
    stats_line = f"Stats: total new {total_new} | Canada {canada_new} | USA/other {other_new}"
    if not rows:
        return f"{stats_line}\nNo new matching jobs today."
    lines = [stats_line]
    for r in rows:
        company = r.get("company", "")
        role = r.get("role", "")
        location = r.get("location", "")
        link = r.get("application_url", "")
        if not link:
            app = r.get("application", "")
            url_match = re.search(r"https?://\S+", app)
            if url_match:
                link = url_match.group(0)
        line = f"{company} — {role} — {location}"
        if link:
            line += f" — {link}"
        lines.append(line)
    return "\n".join(lines)


def format_html(
    rows: List[Dict[str, str]],
    total_new: int,
    canada_new: int,
    other_new: int,
) -> str:
    stats_line = f"Stats: total new {total_new} | Canada {canada_new} | USA/other {other_new}"
    if not rows:
        return f"""
        <html>
          <body>
            <p>{html.escape(stats_line)}</p>
            <p>No new matching jobs today.</p>
          </body>
        </html>
        """.strip()

    lines = []
    for r in rows:
        company = html.escape(r.get("company", ""))
        role = html.escape(r.get("role", ""))
        location = html.escape(r.get("location", ""))
        link = r.get("application_url", "")
        if not link:
            app = r.get("application", "")
            url_match = re.search(r"https?://\S+", app)
            if url_match:
                link = url_match.group(0)
        link_html = ""
        if link:
            link_html = (
                f"<a href=\"{html.escape(link)}\" "
                "style=\"display:inline-block;padding:6px 10px;"
                "background:#2563eb;color:#ffffff;text-decoration:none;"
                "border-radius:4px;font-size:12px;\">Apply</a>"
            )
        lines.append(
            f"<li><strong>{company}</strong> — {role} — {location} {link_html}</li>"
        )

    return f"""
    <html>
      <body>
        <p>{html.escape(stats_line)}</p>
        <ul>
          {''.join(lines)}
        </ul>
      </body>
    </html>
    """.strip()


def send_email_smtp(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    to_email: str,
    from_email: str,
    subject: str,
    text_body: str,
    html_body: str,
) -> None:
    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    if not smtp_host:
        raise ValueError("SMTP_HOST is required and cannot be empty")

    use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() in {"1", "true", "yes"}
    if use_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)


def env_list(name: str) -> Optional[List[str]]:
    val = os.getenv(name)
    if not val:
        return None
    return [v.strip() for v in val.split(',') if v.strip()]


def main():
    smtp_host = os.getenv("SMTP_HOST") or "smtp.gmail.com"
    smtp_port_value = os.getenv("SMTP_PORT")
    smtp_port = int(smtp_port_value) if smtp_port_value else 587
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    to_email = os.getenv("EMAIL_TO")
    from_email = os.getenv("EMAIL_FROM")
    if not smtp_user or not smtp_password or not to_email or not from_email:
        raise SystemExit("Missing SMTP_USER, SMTP_PASSWORD, EMAIL_TO, or EMAIL_FROM")

    branch = os.getenv("SOURCE_BRANCH", "dev")
    allow_locations = env_list("LOCATION_ALLOWLIST")
    filter_by_location = os.getenv("FILTER_BY_LOCATION", "true").lower() in {"1", "true", "yes"}
    include_keywords = env_list("INCLUDE_KEYWORDS")
    exclude_keywords = env_list("EXCLUDE_KEYWORDS")

    lookback_hours_value = os.getenv("LOOKBACK_HOURS")
    lookback_hours = int(lookback_hours_value) if lookback_hours_value else 24
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    since_iso = since.isoformat(timespec="seconds")

    shas = get_commits_since_time(branch, since_iso, limit=100)
    latest_sha = shas[0] if shas else branch
    current_readme = fetch_readme(ref=latest_sha)
    current_rows = parse_tables(current_readme)
    if not current_rows:
        raise SystemExit("No tables found in current README")

    previous_rows: List[Dict[str, str]] = []
    if shas and len(shas) > 1:
        previous_ref = shas[-1]
        prev_readme = fetch_readme(ref=previous_ref)
        previous_rows = parse_tables(prev_readme)

    new_rows = diff_new_rows(current_rows, previous_rows)
    filtered = filter_rows(new_rows, allow_locations if filter_by_location else None, include_keywords, exclude_keywords)
    total_new, canada_new, other_new = count_locations(new_rows)
    text_body = format_plain(filtered, total_new, canada_new, other_new)
    html_body = format_html(filtered, total_new, canada_new, other_new)
    subject = f"Summer 2026 internships digest (new: {len(filtered)})"
    send_email_smtp(
        smtp_host,
        smtp_port,
        smtp_user,
        smtp_password,
        to_email,
        from_email,
        subject,
        text_body,
        html_body,
    )


if __name__ == "__main__":
    main()
