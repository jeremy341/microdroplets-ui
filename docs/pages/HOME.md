# Home Page

The Home page is the application's overview/dashboard. It summarizes the currently selected Multiboard and high-level subsystem status; it is not a second control backend.


For board lifecycle and runtime service creation, see [Software Architecture](../SOFTWARE_ARCHITECTURE.md).

## What the user sees

When no board is selected, Home shows an empty-state message. With a connected board it can display information such as:

- board/device name;
- connection status;
- COM port;
- firmware version;
- pump-driver count/summary;
- valve-driver count/summary;
- sensor count/summary;
- recent application activity.

## Intended use

Use Home to answer “what is connected and what is the application currently aware of?” before entering a subsystem page.

Do not use Home as the authoritative place for changing pump, sensor, wave, or camera settings.

## Developer notes

Implementation: `ui/pages/Home.py`.

The root application (`app.py`) owns connection lifecycle and passes the active/connected board model into all pages. Home should reflect that state rather than create parallel connection logic.

When adding a new summary item:

1. obtain the value from the existing backend/page state;
2. keep the display read-only unless the action truly belongs on Home;
3. avoid duplicating hardware logic already implemented elsewhere.

## Navigation

Home is the default page at startup.

Keyboard shortcut:

```text
Shift+1
```
