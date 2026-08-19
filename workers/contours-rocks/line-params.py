#!/usr/bin/env python3
"""
Čo z mriežky rastra vyplýva pre čiaru: tolerancia zjednodušenia a σ vyhladenia.

JEDNA OTÁZKA, JEDNO MIESTO. Obe čísla stoja na tom istom – na tom, aká hrubá
je bunka rastra, z ktorého sa vrstevnica NAOZAJ trasovala. Kým to bol heredoc
v `build.sh`, nedalo sa to spustiť ani skontrolovať bez celého behu, a `build.sh`
narazil na strop 800 riadkov (pravidlo 5 v CLAUDE.md).

Vypíše štyri čísla oddelené medzerou:

    <tolerancia v stupňoch>  <tá istá v metroch>  <σ v metroch>  <bunka v metroch>

Použitie (tak to volá `contours-rocks/build.sh`):
    python3 workers/contours-rocks/line-params.py -1 work/clip.tif 3
"""
import json
import subprocess
import sys

want = float(sys.argv[1])
raster = sys.argv[2]
line_cells = float(sys.argv[3])
# DLHŠÍ z dvoch stupňov – ten po ŠÍRKE (110 540 m). Stupeň po dĺžke má u nás
# len ~73 000 m, takže je to on, kto rozhoduje o najhoršom prípade, a ten sa
# tu musí použiť dvakrát:
#   metre → stupne  … väčší deliteľ dá menšiu toleranciu, čiže na zemi nikdy
#                     nebude väčšia, než sa žiadalo, nech ide svah akokoľvek
#   stupne → metre  … do logu ide najväčšia možná, nie najmenšia; opačne by
#                     riadok tvrdil menšiu toleranciu, než sa naozaj použila
m_per_deg = 110540
# Mriežka rastra, z ktorého sa NAOZAJ trasovalo. Pýta sa jej aj tolerancia
# zjednodušenia, aj σ vyhladenia po čiare – jedna otázka, jedna odpoveď.
cell_deg = 0.0
if want < 0 or line_cells > 0:
    info = json.loads(subprocess.run(
        ["gdalinfo", "-json", raster],
        capture_output=True, text=True, check=True).stdout)
    gt = info["geoTransform"]
    cell_deg = min(abs(gt[1]), abs(gt[5]))
cell_m = cell_deg * m_per_deg
if want == 0:
    deg = 0.0
elif want > 0:
    deg = want / m_per_deg          # zadané v metroch
else:
    # -1 = štvrtina bunky, -2 = polovica, -4 = celá. Nad polovicou sa čiara
    # začína odliepať od terénu (merané: pri 3/4 bunky vyskočí odchýlka od
    # skutočnej izolínie zo 0,58 na 1,29 bunky), tak sa vyššie nechodí.
    deg = cell_deg * (-want) / 4
# σ VYHLADENIA PO ČIARE. V BUNKÁCH, lebo otázka je „čo model nemá z čoho
# vedieť": pod dvomi bunkami nie je v izolínii terén, ale šum merania,
# vegetácia v DTM a interpolácia. Strop v metroch chráni HRUBÉ modely – pri
# Sonnyho 20 m by tri bunky boli 60 m a to už je tvar, ktorý človek v mape
# číta ako terén; 30 m je zhruba to, čo je pri z16 dvadsať pixelov.
SIGMA_MAX_M = 30.0
sigma_m = min(line_cells * cell_m, SIGMA_MAX_M) if line_cells > 0 else 0.0
print(f"{deg:.10f} {deg * m_per_deg:.2f} {sigma_m:.2f} {cell_m:.2f}")
