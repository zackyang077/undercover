import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { Activity } from "./Activity";

it("shows the full versioned section the helper read", () => {
  render(
    <Activity
      runs={[
        {
          thread_id: "thread",
          status: "completed",
          started_at: "",
          error: "",
          events: [
            {
              id: 1,
              kind: "section_read",
              tool_name: "read_guidance_section",
              created_at: "2026-11-16T01:00:00Z",
              text: JSON.stringify({
                document: "manual",
                version: "2026.2",
                section_id: "10",
                title: "Meals without receipts",
                content: "Synthetic complete section: $35 for a traveler.",
              }),
            },
          ],
        },
      ]}
    />,
  );
  fireEvent.click(
    screen.getByText(
      "Manual section 10: Meals without receipts · Version 2026.2",
    ),
  );
  expect(
    screen.getByText("Synthetic complete section: $35 for a traveler."),
  ).toBeDefined();
});
