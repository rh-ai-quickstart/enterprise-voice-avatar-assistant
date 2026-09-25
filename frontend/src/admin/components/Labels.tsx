import { Label } from "@patternfly/react-core";
import { duration, words } from "../format";
import type { Sla, TicketStatus } from "../types";

type Color = "blue" | "green" | "orange" | "red" | "grey" | "purple" | "teal" | "yellow" | "orangered";

const STATUS_COLORS: Record<TicketStatus, Color> = {
  intake: "grey",
  classified: "grey",
  pending_approval: "orange",
  approved: "blue",
  fulfilled: "green",
  rejected: "red",
  cancelled: "grey",
};

export function StatusLabel({ status }: { status: TicketStatus }) {
  return (
    <Label color={STATUS_COLORS[status] ?? "grey"} isCompact>
      {words(status)}
    </Label>
  );
}

/** How long a request has waited: grey, amber past the SLA reminder, red past the escalation. */
export function SlaBadge({ minutes, sla }: { minutes: number | null; sla: Sla }) {
  if (minutes === null) return null;
  const color: Color = minutes >= sla.escalation_minutes ? "red" : minutes >= sla.reminder_minutes ? "orange" : "grey";
  const why =
    color === "red"
      ? `past the escalation after ${duration(sla.escalation_minutes)}`
      : color === "orange"
        ? `past the reminder after ${duration(sla.reminder_minutes)}`
        : `reminder after ${duration(sla.reminder_minutes)}`;
  return (
    <Label color={color} isCompact title={why}>
      waiting {duration(minutes)}
    </Label>
  );
}

const SEVERITY_COLORS: Record<string, Color> = { info: "blue", success: "green", warning: "orange", error: "red" };

export function SeverityLabel({ severity }: { severity: string }) {
  return (
    <Label color={SEVERITY_COLORS[severity] ?? "grey"} isCompact>
      {severity}
    </Label>
  );
}
