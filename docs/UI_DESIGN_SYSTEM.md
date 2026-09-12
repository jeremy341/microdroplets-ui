# UI Design System and PyQt/QSS Conventions

This document records the visual and interaction conventions used across
FluidicStudio so a future developer does not have to infer the design system
from a large QSS/UI surface whose effective styling is split across QSS, design
tokens, and page-local code.

The visual sources of truth are:

```text
style.qss
ui/design_tokens.py
ui/pages/*.py
```

This is a documentation source-of-truth statement, not proof that the sources
are currently fully centralized: Analytics still contains inline stylesheet
rules, and `style.qss` has selector/redefinition drift that should be reconciled
before a future UI redesign.

## 1. Design direction

FluidicStudio uses a restrained laboratory-control UI:

- light neutral application background;
- pale green sidebar;
- white cards;
- green accent for active/selected controls;
- red/danger styling only for stop/error/blocking states where it adds meaning;
- compact radii and consistent spacing;
- minimal decorative UI;
- controls should explain hardware state rather than look like a generic dashboard.

The interface should remain functional at normal desktop widths and Workspace
panels must remain usable at roughly half the content width.

## 2. Core layout tokens

`ui/design_tokens.py` currently defines:

| Token | Value |
| --- | ---: |
| page gutter | 32 px |
| page top | 16 px |
| page bottom | 24 px |
| section gap | 16 px |
| card gap | 16 px |
| card padding | 16 px |
| standard control height | 38 px |
| primary control height | 54 px |
| board control height | 54 px |
| row height | 48 px |
| radius | 6 px |
| sidebar width | 250 px |
| navigation row height | 44 px |

Prefer these shared values when adding structurally similar layouts instead of
creating a new random spacing scale.

Audit note: `ui/design_tokens.py` defines a standard control height of 38 px,
while parts of `style.qss` describe a 48 px standard control. Treat the token
file as the intended value and verify the rendered result before documenting a
new component as compliant.

## 3. Main palette

Current QSS uses approximately:

```text
app background     #F8FAF9
sidebar             #EEF5F2
primary text        #29332F / #26322D
green accent        #368A6C and related hover/pressed values
card borders        #DEE6E2 / #D5DFDA
muted/disabled      neutral gray-greens
```

Do not add a second unrelated accent color for ordinary selection states.

## 4. QSS object-name strategy

FluidicStudio relies heavily on Qt `objectName` selectors:

```text
#driverCard
#parameterSpinBox
#parameterSlider
#cameraCombo
#workspacePanelShell
#workspacePumpPanel
...
```

When creating a new control that should look identical to an existing component,
prefer using the existing object-name/class convention rather than copying an
entire style block with a new name.

When a component genuinely needs page-specific behavior, scope the QSS under the
page/panel object name so it cannot leak globally.

Example concept:

```text
#workspacePumpPanel QSpinBox#workspaceSpin { ... }
```

## 5. Dynamic properties for state styling

Some UX states are represented with Qt dynamic properties and QSS rather than
hard-coded `setStyleSheet()` strings.

Examples include concepts like:

```text
pumpStopped=true
blockedByPump=true
state=invalid
```

When changing such a property, remember that Qt may require style
unpolish/polish/update logic for the new state to render immediately.

Prefer dynamic properties over repeatedly concatenating large inline style
strings.

## 6. Buttons

Default button hierarchy:

### Normal/secondary

White surface, neutral border, green hover/focus.

### Primary

Use only for the main action in a section. Do not make every button green.

### Danger/stop

Reserved for stop/destructive/error-recovery actions.

Do not add separate Start/Stop buttons when an existing ON/OFF switch already
communicates and performs that state transition. This was specifically cleaned up
in Pump Workspace.

## 7. Sliders and spin boxes

For numeric hardware controls, sliders and value spin boxes normally represent
the same underlying value.

Important UX rule from the Workspace pump fixes:

> Periodic backend synchronization must not overwrite a slider/spin box while
> the user is actively dragging or typing.

Hardware ACK latency should use pending/optimistic UI state rather than visible
snap-back when possible.

## 8. Pump stopped-state UX

The full Pumps page established this convention:

```text
pump OFF
→ amplitude slider/value looks muted/grey
→ amplitude is still editable as a staged value

pump ON
→ amplitude returns to active styling
```

Do not equate “grey-looking” with `setEnabled(False)` in this case.

Frequency remains a driver-level setting. Signal/carrier controls follow their
own backend availability rules.

Workspace Pump should mirror these semantics.

## 9. Wave runtime UX

The Wave editor uses state-dependent controls:

### Free channel

- editor editable;
- Test enabled;
- Stop disabled.

### Selected channel manually owned

- Test disabled;
- button explains `Pump in use`;
- compatibility/status area explains why.

### Wave running

- Wave definition/channel controls lock;
- Stop enabled;
- driver frequency stays live-editable under current backend policy.

Workspace Wave mirrors this behavior without changing its compact layout.

## 10. Camera layout and preview behavior

The full Camera page has more horizontal room than the Workspace version.
Workspace can use a compact arrangement but must not create a second camera
runtime.

Preview behavior:

- full/raw frame remains unchanged;
- Workspace preview may use `KeepAspectRatioByExpanding` + center crop;
- never stretch the image just to fill a box;
- preview crop is presentation-only and must not affect saved photo/video or
  Analytics input.

## 11. Workspace design rules

Workspace is intentionally a half-width/half-width experiment surface.

Current behavior:

```text
initial split ≈ 50/50
→ user may drag QSplitter
→ chosen ratio is not constantly forced back to 50/50
```

The initial ratio is a runtime presentation default, not guaranteed persisted
experiment state.

Each compact panel should prioritize the controls needed during an experiment.
Do not try to reproduce every full-page control at half width.

Current panels:

```text
Pumps
Sensors
Wave
Camera
```

### Pump Workspace

Maximum two visible channel slots is the current compact design.

Each slot contains:

- channel selector;
- ON/OFF switch;
- driver frequency;
- signal mode if supported;
- amplitude.

No extra Start/Stop buttons.

## 12. Full page and Workspace parity

Parity means **same meaning and state**, not necessarily identical geometry.

Example:

```text
full Pump page: wide card
Workspace Pump: compact stacked card
```

They can differ in layout while still showing the same:

- physical state;
- pending state;
- ownership state;
- values;
- disabled/blocked meaning.

Do not solve a Workspace sizing issue by changing the backend behavior.

## 13. Page titles and global header

`app.py` owns the universal page title/header. Pages should not independently
recreate a second global header.

Workspace can update the global title according to its current workspace state.

## 14. Scroll policy

Prefer fitting core experiment controls without unnecessary scrolling at normal
desktop sizes. Use scrolling for genuinely long lists/configuration rather than
because spacing is oversized.

Workspace especially should remain compact enough to be useful beside another
panel.

## 15. Disabled controls need meaning

A disabled control should have a reason the user can infer from nearby state,
button text, banner or tooltip.

Examples:

```text
Pump in use
Wave running
No board connected
Carrier locked while driver active
```

Avoid a screen full of unexplained gray widgets.

## 16. Do not expose unvalidated hardware controls

The UI is not an API browser.

Examples:

- DNX64 FLC/LED intensity exists at a low level but was removed from product UI
  because current hardware behavior was not sufficiently validated;
- automatic driver fingerprinting exists as diagnostics but is not a production
  selector.

Only expose controls whose semantics are known and backed by the service layer.

Current UI audit limitations include mouse-only custom Sensors controls,
drag-only Analytics setup, missing names for several custom Workspace controls,
and focus outlines that are inconsistent or suppressed by QSS. These are UI
backlog items; this document does not authorize changing them in a backend-only
fix.

## 17. Adding a new component

Before writing new QSS:

1. find the nearest existing component type;
2. reuse design tokens;
3. reuse existing object-name styling if semantics match;
4. use a page/panel-scoped selector for the exception;
5. keep hover/focus/disabled states consistent;
6. test full page and Workspace widths where relevant;
7. avoid source-string tests for purely cosmetic details unless necessary.

## 18. Styling files to read for a page

```text
style.qss
ui/design_tokens.py
ui/pages/<Page>.py
```

Then read the corresponding page document under `docs/pages/` before changing
interaction semantics.
