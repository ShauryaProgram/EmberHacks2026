# Semester Workspace design standards

## Direction

Semester Workspace is a quiet Modernist planning tool for students. The interface should feel precise, calm, and direct. Its design dials are ENERGY 1, RHYTHM 2, and MOTION 1.

## Ground and structure

- Use pure white as the light-mode ground and `#131211` as the dark-mode ground.
- Place content directly on the page. Do not introduce cards or tinted surface containers for ordinary content.
- Use a 2px `--rule` only at major region edges, including the sidebar, calendar header, and planner divider.
- Use a 1px `--hairline` between repeated rows.
- Use shadows only for content that genuinely floats above the page: dialogs, search, the detail panel, and transient menus.

## Typography

- Use Archivo for all interface text.
- Page title: 29px, weight 700, tracking `-0.025em`.
- Section label: 12px, weight 600, uppercase, tracking `0.09em`.
- Task title: 15px, weight 500, tracking `-0.02em`.
- General UI: 13.5px.
- Metadata: 12.5px.
- Use weight and spacing for hierarchy before adding color or larger size jumps.

## Color

- Keep the interface neutral. Ink and ground carry nearly all hierarchy.
- Reserve red for overdue information, High priority, errors, and the current-time line.
- Use muted course colors only as 6px identifiers and pale calendar tints.
- Do not use decorative gradients. The live Voice beam is the only gradient because it visualizes microphone input.

## Shape

- Default to square corners for the shell, controls, inputs, rows, and overlays.
- Do not turn structural regions into rounded cards.
- A component may retain a small control radius when that shape communicates its interaction, as in Checklist completion controls.

## Spacing and width

- Use 56px to 64px desktop page margins and 44px between major sections.
- Use about 10px of vertical padding inside repeated rows.
- Keep Today near 760px, Tasks near 900px, and course pages near 800px. Calendar and Planner may use the full available width.
- Related information stays close. Unrelated decisions receive a full section gap.

## Interaction and motion

- Show task title and one metadata line by default. Reveal occasional actions on hover or keyboard focus.
- Keep motion short and state-driven. Do not use endless loops.
- Checklist motion may connect checkbox fill, tick, strike, and text fade because all four communicate one completion event.
- Respect `prefers-reduced-motion`.

## Checklist

- Label the feature `Checklist`.
- Place it inside the normal Tasks page flow, before the detailed task groups.
- Drive it from the first five open tasks in the current filtered and sorted view.
- Keep the detailed list available for course, due date, priority, scheduling, and editing information.
- Map Bencho tokens locally to the existing ground, ink, hairline, accent, and Archivo tokens. Do not add a second global design system.

## Voice

- Label the feature `Voice`.
- Open it with Command J on macOS and Control J elsewhere.
- Start microphone capture from that user gesture.
- Show only the live transcript, status, and close instruction until task creation or question handling is implemented.
- Keep the VoiceBeam effect inside the voice sheet and only active while listening.

## Responsive and accessible behavior

- Preserve keyboard access, visible focus, semantic headings, and honest empty states.
- Keep touch targets at least 44px where practical on mobile.
- Test light and dark themes, desktop and mobile widths, keyboard-only use, and reduced motion before release.
