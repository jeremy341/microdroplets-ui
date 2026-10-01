# Driver probe test

This is a read-only diagnostic for the real MP-Multiboard2. It sends only:

```text
V
P1V?
P2V?
P3V?
P4V?
P5V?
P6V?
```

It does not start a pump, change amplitude, stop pumps, or start the sensor
stream. Close FluidicStudio and the PyQt app first.

```powershell
cd "Z:\Lin\Jeremy Darko\Microdroples Ui"
python -m pip install -r requirements.txt
python bartels_driver_probe_test.py --port COM3
```

Send the complete terminal output back. The CSV is written to:

```text
Documents\bartels\driver_probe\driver_probe_YYYYMMDD_HHMMSS.csv
```
