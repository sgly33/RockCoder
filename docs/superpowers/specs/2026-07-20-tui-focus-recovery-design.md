# Unified TUI Focus Recovery Design

## Goal

Make focus recovery consistent for the existing result-display widgets that can steal keyboard focus in the chat area, without changing modal widgets that intentionally own input.

## Scope

This change covers only these widgets in `mewcode/app.py`:
- `ToolCallBlock`
- `ToolGroupSummary`
- `SubAgentBlock`

It does not change:
- `InlinePermissionWidget`
- `InlinePlanWidget`
- `InlineAskUserWidget`
- chat-area blank-space clicks
- generic `Static`/`Markdown` message widgets

## Chosen Approach

Use a small shared helper or mixin-level behavior for the three result-display widgets so that, after their click behavior runs, they restore focus to `#chat-input` if and only if the input exists and is not disabled.

This keeps the behavior narrow and explicit:
- only known focus-stealing result blocks participate
- modal ownership remains intact
- no app-level click routing or broad chat-area interception is introduced

## Behavior Rules

1. Clicking one of the three result-display widgets may still perform its normal UI action:
- expand/collapse tool output
- toggle grouped tool visibility
- expand/collapse subagent output

2. After that action completes, the widget attempts to restore focus to `#chat-input`.

3. Focus restoration must be skipped when:
- `#chat-input` cannot be found
- `#chat-input.disabled` is `True`

4. No other widget gains this behavior in this change.

## Why This Approach

### Recommended: shared helper on the participating widgets
Pros:
- smallest safe change
- preserves current widget-local behavior
- avoids coupling `MewCodeApp` to child widget type checks
- avoids changing modal flows

Cons:
- future result widgets must opt in explicitly

### Rejected: app-level click interception
Reason:
- makes `MewCodeApp` aware of child display-widget categories
- broadens the behavior surface beyond the bug we are fixing

### Rejected: whole-chat-area click-to-refocus
Reason:
- changes more user interactions than requested
- risks fighting future widgets that legitimately want focus

## Testing

Keep tests narrow and behavior-based.

Required regression coverage:
- clicking `ToolCallBlock` restores focus to `#chat-input`
- clicking `ToolGroupSummary` restores focus to `#chat-input`
- clicking `SubAgentBlock` restores focus to `#chat-input`
- when `#chat-input` is disabled, the restore helper does not force focus back

## Risks

Main risk: future focus-stealing display widgets may be added without opting into the helper.

This is acceptable for now because the chosen scope is intentionally narrow, and each new focusable result widget should make an explicit decision about whether it participates in chat-input focus recovery.
