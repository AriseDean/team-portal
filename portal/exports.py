import csv
import io
import tempfile
import zipfile

from django.http import FileResponse, HttpResponse
from django.utils import timezone
from django.utils.text import slugify


def csv_response(filename, header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    response = HttpResponse("﻿" + buf.getvalue(), content_type="text/csv; charset=utf-8")  # BOM: opens cleanly in Excel
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def zip_response(filename, uploads):
    """Zip the given Upload rows (spooled to disk so hundreds of reports never sit in memory)."""
    tmp = tempfile.TemporaryFile()
    manifest = [["file", "team_id", "team", "leader", "version", "uploaded_by", "enrolment", "uploaded_at", "late"]]
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
        for up in uploads:
            team = up.submission.team
            ext = up.file.name.rsplit(".", 1)[-1]
            arcname = f"{team.team_id}_{slugify(team.name)[:40]}.{ext}"
            with up.file.open("rb") as fh:
                zf.writestr(arcname, fh.read())
            manifest.append([
                arcname, team.team_id, team.name, team.leader_name, up.version,
                up.uploaded_by_name, up.uploaded_by_enrolment,
                timezone.localtime(up.uploaded_at).strftime("%Y-%m-%d %H:%M"), "yes" if up.is_late else "",
            ])
        out = io.StringIO()
        csv.writer(out).writerows(manifest)
        zf.writestr("_manifest.csv", "﻿" + out.getvalue())
    tmp.seek(0)
    return FileResponse(tmp, as_attachment=True, filename=filename, content_type="application/zip")
