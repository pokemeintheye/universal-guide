# Apple TV guide visual target — reference specification

This is the visual acceptance criteria for the ongoing tvOS redesign, based on the user's uploaded photo (IMG_321146D9-F448-441B-9A0E-48E2A47175C8.jpeg). The photo is the source of truth when accessible; this written specification is a durable fallback. It shows a flat dark-gray live electronic program guide filling the television display.

## Target hierarchy
- Almost the ENTIRE television screen is the grid: no persistent bottom detail panel, prominent branding header, marketing panel, or large surrounding border.
- Minimal top bar: small bold "Today", small pink accent icon, current time at upper left. Time labels aligned over program columns (e.g., 11:30 PM, 12:00 AM).
- Narrow left channel column roughly 13–16% of the visible guide width, made of compact gray rectangular tiles with centered network logos. A text fallback should remain attractive until logo data is available.
- Program cells are wide, medium-charcoal/blue-gray rectangles with subtle 2–5 px gaps and very modest corner radius. White title text is bold and readable. Secondary subtitle/time text is smaller and pale gray.
- Multiple channels visible (roughly 7 rows in the photo), tight height (~86 px for 1080 px wide perspective-normalized screenshot relative scaling); absolutely avoid large empty regions.
- Current show has a thin hot pink/magenta horizontal progress indicator just above the tile's lower edge, consisting of a pale baseline and bright filled fraction.
- Selected program appears IN PLACE in its cell, not in a bottom panel: subtle thin white or very light pink outline, title noticeably larger, episode subtitle and airtime below; right-hand artwork strip fills the last ~15–25% of a wide cell and does not cover the text. Selection never causes entire grid/row to resize.
- Reference shows one selected row "Cupcake Wars" with 3 text lines and a right-side image. It also shows several non-selected live rows and next-program tiles to the right.
- Color palette: mostly dark medium gray/slate with desaturated blue undertone; bright magenta accent limited mainly to progress bars and minimal icon. Avoid large saturated purple panels and thick glowing focus borders.
- Guide should look like a polished native electronic TV guide, NOT an Apple TV streaming app landing page.

## Nonvisual requirements
- Preserve exact existing Plex/Tablo playback functionality, external Philo handoff, and iPhone UI.
- D-pad rapid Up/Down must keep the selected item visible, no skipped/unexpected rows.
- Simulator screenshots use the `--visual-preview` synthetic guide fixture; do not replace production server data.
- Use actual rendered tvOS simulator PNGs for visual comparisons; a successful compilation alone is insufficient.
- Only branch `tvos-auto-visual-development` and draft PR #25 are authorized for automatic updates. Do not merge, release TestFlight, or change secrets without explicit approval.
