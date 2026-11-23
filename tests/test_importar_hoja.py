"""Pruebas de la importación desde la hoja de equipos por nombre de pestaña (libro .xlsx, sin gid)."""
import io
import json
import zipfile
from dataclasses import replace
from xml.sax.saxutils import escape

import pytest

from vgclab import importar as I
from vgclab import regulacion as R


def _col(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def hacer_xlsx(hojas, enlaces=None):
    """Un .xlsx mínimo como los de Google: textos compartidos y, opcionalmente, celdas con hipervínculo."""
    enlaces = enlaces or {}
    shared, idx = [], {}
    def sid(v):
        if v not in idx:
            idx[v] = len(shared)
            shared.append(v)
        return idx[v]
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        wb_sheets, wb_rels = [], []
        for n, (nombre, filas) in enumerate(hojas.items(), 1):
            xml_rows = []
            for r, fila in enumerate(filas, 1):
                cs = "".join(f'<c r="{_col(c)}{r}" t="s"><v>{sid(v)}</v></c>' for c, v in enumerate(fila) if v != "")
                xml_rows.append(f'<row r="{r}">{cs}</row>')
            links = enlaces.get(nombre, {})
            hl = "".join(f'<hyperlink ref="{ref}" r:id="rIdL{k}"/>' for k, ref in enumerate(links))
            f.writestr(f"xl/worksheets/sheet{n}.xml",
                       '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                       f'<sheetData>{"".join(xml_rows)}</sheetData>' + (f"<hyperlinks>{hl}</hyperlinks>" if hl else "") +
                       "</worksheet>")
            if links:
                rels = "".join(f'<Relationship Id="rIdL{k}" Type="hyperlink" Target="{escape(u)}" TargetMode="External"/>'
                               for k, u in enumerate(links.values()))
                f.writestr(f"xl/worksheets/_rels/sheet{n}.xml.rels",
                           f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
            wb_sheets.append(f'<sheet name="{escape(nombre)}" sheetId="{n}" r:id="rId{n}"/>')
            wb_rels.append(f'<Relationship Id="rId{n}" Type="worksheet" Target="worksheets/sheet{n}.xml"/>')
        f.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   f'<sheets>{"".join(wb_sheets)}</sheets></workbook>')
        f.writestr("xl/_rels/workbook.xml.rels",
                   f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{"".join(wb_rels)}</Relationships>')
        f.writestr("xl/sharedStrings.xml", '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   + "".join(f"<si><t>{escape(s)}</t></si>" for s in shared) + "</sst>")
    return z.getvalue()


CABECERA = ["Team ID", "Team Description", "", "Full Name", "Pokepaste"]
HOJAS = {
    "Hi 🐹": [["", "Welcome"]],
    "Champions M-C": [
        ["", "VGCPastes Repository (Champions M-C)"], CABECERA,
        ["MC3", "Ana's Rain Team", "", "Ana", "https://pokepast.es/aaaaaaaaaaaaaaaa"],
        ["MC2", "Bea's Trick Room Team", "", "Bea", "https://pokepast.es/bbbbbbbbbbbbbbbb"],
        ["MC1", "Cris's Sun Team", "", "Cris", "Pokepaste"],                 # el enlace sólo está en el hipervínculo
    ],
    "Champions M-B": [CABECERA, ["MB1", "Dani's Team", "", "Dani", "https://pokepast.es/dddddddddddddddd"]],
}
XLSX = hacer_xlsx(HOJAS, {"Champions M-C": {"E5": "https://pokepast.es/cccccccccccccccc"}})


def test_pestanas_y_equipos():
    assert list(I.xlsx_tabs(XLSX)) == list(HOJAS)
    eq = I.find_teams(I.read_xlsx_tab(XLSX, "Champions M-C"))
    assert [(t["id"], t["paste"]) for t in eq] == [("MC3", "a" * 16), ("MC2", "b" * 16), ("MC1", "c" * 16)]
    assert eq[0]["name"] == "Ana's Rain Team"
    assert len(I.find_teams(I.read_xlsx_tab(XLSX, "  champions m-b "))) == 1       # sin importar mayúsculas


def test_pestana_que_no_existe():
    with pytest.raises(SystemExit, match="Champions M-C"):
        I.read_xlsx_tab(XLSX, "Champions M-D")


class MotorFalso:
    def __init__(self, *a): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def call(self, cmd, **k):
        if cmd == "normalize":
            return k["text"]
        if cmd == "validate":
            ok = "Ilegal" not in k["team"]
            return {"ok": ok, "problems": [] if ok else ["Ilegal no está permitido"]}


def test_importar_de_la_hoja(tmp_path, monkeypatch):
    pastes = {"a" * 16: "Pelipper @ Damp Rock\nAbility: Drizzle\n- Hurricane\n",
              "b" * 16: "Ilegal @ Leftovers\nAbility: Pressure\n- Tackle\n",
              "c" * 16: "Torkoal @ Charcoal\nAbility: Drought\n- Eruption\n"}
    (tmp_path / "MC0_ya_lo_tenia.txt").write_text(pastes["c" * 16], encoding="utf-8")
    monkeypatch.setattr(I, "ROOT", tmp_path)
    monkeypatch.setattr(I, "REG", replace(R.REG, hoja="https://docs.google.com/spreadsheets/d/XYZ/edit", pestana="Champions M-C"))
    pedidas = []
    monkeypatch.setattr(I, "http_get_bytes", lambda url, *a, **k: pedidas.append(url) or XLSX)
    monkeypatch.setattr(I, "fetch_paste", lambda pid, cache: pastes[pid])
    monkeypatch.setattr(I, "Engine", MotorFalso)
    I.main(["--hoja", "--salida", str(tmp_path)])
    assert pedidas == ["https://docs.google.com/spreadsheets/d/XYZ/export?format=xlsx"]
    nuevos = sorted(p.name for p in tmp_path.glob("*.txt"))
    assert nuevos == ["MC0_ya_lo_tenia.txt", "MC3_Anas_Rain_Team.txt"]
    informe = (tmp_path / "_informe_importacion.csv").read_text(encoding="utf-8")
    assert "ya estaba" in informe and "no válido" in informe


def test_sin_hoja_avisa(monkeypatch):
    monkeypatch.setattr(I, "REG", replace(R.REG, hoja=None))
    with pytest.raises(SystemExit, match="no tiene hoja"):
        I.main(["--hoja"])


def test_regulacion_hoja(tmp_path, monkeypatch):
    monkeypatch.delenv("VGCLAB_REG", raising=False)
    monkeypatch.delenv("VGCLAB_FORMAT", raising=False)
    monkeypatch.setattr(R, "ROOT", tmp_path)
    monkeypatch.setattr(R, "ARCHIVO", tmp_path / "regulacion.json")
    R.ARCHIVO.write_text(json.dumps(R._POR_DEFECTO), encoding="utf-8")
    assert R.activa().pestana == "Champions M-C Featured Teams" and "1axlwmzP" in R.activa().hoja
    assert R.pestana_por_defecto("Reg M-D") == "Champions M-D Featured Teams"
    R.main(["--nueva", "md", "--nombre", "Reg M-D", "--formato", "gen9championsvgc2026regmd", "--sin-comprobar"])
    md = R.activa()
    assert md.hoja == R._de(R._POR_DEFECTO, "mc").hoja and md.pestana == "Champions M-D Featured Teams"   # hereda la hoja
    R.main(["--pestana", "Champions M-D"])
    assert R.activa().pestana == "Champions M-D"
    with pytest.raises(SystemExit):
        R.main(["--hoja", "https://example.com/no-es-una-hoja"])
