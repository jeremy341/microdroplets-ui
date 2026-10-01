# Pump terminal test

Close Bartels FluidicStudio before running the test so that it does not own the
same COM port. Connect the MP-Multiboard2 by USB, power the board and the
appropriate driver, and connect only one pump for the first test.

Install the project dependencies and run:

```powershell
python -m pip install -r requirements.txt
python bartels_pump_test.py --port COM3 --channel 1
```

The default profile is 100 Hz and 50 Vpp. The program sends `POFF` before
configuration and after stopping. It never starts the pump automatically.
Press Enter only when you are ready to start and again when you want to stop.

The serial log is saved with a unique filename under the user's `Documents`
folder. Existing logs are never overwritten.

Use the driver documentation for the actual connector pinout; do not connect
electrical pins by guessing.
