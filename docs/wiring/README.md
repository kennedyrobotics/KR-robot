# Wiring diagrams

The harness and wiring diagrams for KR-Robot are kept as text and built with [WireViz](https://github.com/wireviz/WireViz). Each diagram is a small YAML file describing:

- the connectors, with their pins
- the cables, with wire colours and gauges
- the connections between them

WireViz renders each one to an SVG for the docs, and a bill of materials (BOM). Because the sources are text, the diagrams live in git, show up in diffs and get reviewed like code.

**Where each kind of wiring information lives:**

| What you need | Where |
|---|---|
| Pin-level electrical rules, pin allocation, and per-interface ICD | [../wiring-manual.md](../wiring-manual.md) |
| BeagleY-AI header pinout diagrams | `../images/beagley-ai-header*.svg` |
| Physical harnesses: what plugs into what, with which wire | **This folder** |

## Diagrams

| Diagram | Source | Shows | Status |
|---|---|---|---|
| ![motor drive](krc-motor-drive.svg) | [krc-motor-drive.yml](krc-motor-drive.yml) | BeagleY-AI header UART → Yahboom YB-ESF01 → left/right track motors, and battery → Yahboom power | **As built 2026-10-02.** The UART link and M1/M2 drive are verified. Connector types, pin order on the Yahboom UART header, wire colours and lengths are **TO VERIFY / record** |

BOM: [krc-motor-drive.bom.tsv](krc-motor-drive.bom.tsv)

**Planned:**

- power distribution: switch → 5 V buck → BeagleY-AI, plus the LiPo alarm
- the I2C sensor loom (IMU, INA226, PCA9685)
- the ultrasonic looms
- the hardware E-stop loop

These follow [../wiring-manual.md](../wiring-manual.md) §5.

## Editing and regenerating

**Setup, once:**

| Platform | Commands |
|---|---|
| Windows | `py -3 -m pip install --user wireviz` and `winget install Graphviz.Graphviz` |
| Debian / BeagleY-AI | `pip install --user --break-system-packages wireviz` and `sudo apt install graphviz` |

**Workflow:**

1. Edit the `.yml`. The [WireViz syntax reference](https://github.com/wireviz/WireViz/blob/master/docs/syntax.md) covers the format.
2. Run `python docs/wiring/build.py` (all diagrams), or `python docs/wiring/build.py krc-motor-drive.yml` (just one).
3. Commit the `.yml`, the regenerated `.svg` and the `.bom.tsv` **together**.

`build.py` keeps only the SVG and the BOM. On Windows it finds Graphviz in its default install folder even when it isn't on PATH.

## Conventions

| Item | Rule |
|---|---|
| Designators | **X** connector/terminal, **W** cable or wire bundle. Fuses, switches and other in-line parts go under `additional_components` on the cable they sit in, so the BOM counts them without adding diagram columns |
| One box per board | Model a board with several headers (e.g. the Yahboom) as **one** connector whose pin labels are grouped by function (`UART …`, `M1±`, `PWR ±`). That keeps the diagram reading left to right: controller → board → loads |
| Wire colours | WireViz codes (`BK RD OG YE GN BU VT GY WH BN PK`). Use **RD/BK** for power +/−, **BK** for signal ground. Codes not yet confirmed on the robot are noted as "suggested – record as-built" |
| Gauges | Signal 26 AWG, motor leads 20 AWG, battery feed 16 AWG (for the ~10 A fuse). Change a gauge only with a reason in the notes |
| Lengths | Leave `length` out until measured, so the BOM shows 0 m. Add the as-built length in metres once measured |
| Unconfirmed facts | Write **TO VERIFY** in the `subtype`/`notes`, and add the matching test case from [../test-validation.md](../test-validation.md) (e.g. TC-A-05). Remove the marker only when the fact has been checked |
| Text | **No bare `<` or `>`** in labels or notes. WireViz 0.4 passes them to Graphviz unescaped, which breaks the build. Use `→ ≤ ≥` instead |
| Safety | Show every pin that must **not** be connected (e.g. the Yahboom UART 5V) with "NOT CONNECTED" in its label, rather than hiding it |
