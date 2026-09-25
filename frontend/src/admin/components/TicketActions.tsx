import {
  Button,
  Flex,
  Form,
  FormGroup,
  FormSelect,
  FormSelectOption,
  Modal,
  ModalBody,
  ModalFooter,
  ModalHeader,
  TextArea,
} from "@patternfly/react-core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { words } from "../format";
import { CATEGORIES, PRIORITIES, type TicketAction, type TicketActionResult } from "../types";
import { useToast } from "./Toasts";

interface TicketLike {
  ticket_ref: string;
  title: string;
  priority: string;
  category: string | null;
}

type Dialog = "reject" | "cancel" | "fulfil" | "edit" | "message" | null;

/**
 * The buttons for what the portal may do with a ticket now (the RAG API lists them in `actions`),
 * with the dialogs that ask for a reason, a confirmation or the new values.
 */
export function TicketActions({ ticket, actions }: { ticket: TicketLike; actions: TicketAction[] }) {
  const client = useQueryClient();
  const toast = useToast();
  const [dialog, setDialog] = useState<Dialog>(null);
  const ref = ticket.ticket_ref;

  const run = useMutation({
    mutationFn: (action: () => Promise<TicketActionResult>) => action(),
    onSuccess: () => {
      setDialog(null);
      client.invalidateQueries({ queryKey: ["tickets"] });
      client.invalidateQueries({ queryKey: ["ticket", ref] });
      client.invalidateQueries({ queryKey: ["overview"] });
    },
    onError: (error: Error) => toast("danger", `${ref}: ${error.message}`),
  });
  // What it means when the RAG API could not reach n8n afterwards
  const FULFILMENT = "The workflow could not be reached: mark it fulfilled once it is done.";
  const CARD = "The workflow could not be reached, so the Slack card, if any, still shows the buttons.";
  const act = (action: () => Promise<TicketActionResult>, said: string, unreachable = CARD) =>
    run.mutate(action, {
      onSuccess: (result) =>
        result.workflow_notified === false
          ? toast("warning", `${said} ${unreachable}`)
          : toast("success", said),
    });

  const approve = () => act(() => api.decide(ref, "approved"), `${ref} approved; the workflow is fulfilling it.`, FULFILMENT);
  const busy = run.isPending;
  const has = (a: TicketAction) => actions.includes(a);

  return (
    <>
      <Flex spaceItems={{ default: "spaceItemsSm" }} flexWrap={{ default: "wrap" }}>
        {has("approve") && (
          <Button variant="primary" size="sm" onClick={approve} isDisabled={busy} isLoading={busy && dialog === null}>
            Approve
          </Button>
        )}
        {has("reject") && (
          <Button variant="danger" size="sm" onClick={() => setDialog("reject")} isDisabled={busy}>
            Reject
          </Button>
        )}
        {has("fulfil") && (
          <Button variant="secondary" size="sm" onClick={() => setDialog("fulfil")} isDisabled={busy}>
            Mark fulfilled
          </Button>
        )}
        {has("edit") && (
          <Button variant="secondary" size="sm" onClick={() => setDialog("edit")} isDisabled={busy}>
            Edit
          </Button>
        )}
        {has("message") && (
          <Button variant="secondary" size="sm" onClick={() => setDialog("message")} isDisabled={busy}>
            Message requester
          </Button>
        )}
        {has("cancel") && (
          <Button variant="link" size="sm" isDanger onClick={() => setDialog("cancel")} isDisabled={busy}>
            Cancel request
          </Button>
        )}
      </Flex>
      {dialog === "reject" && (
        <TextDialog
          title={`Reject ${ref}`}
          intro={`${ticket.title}. The requester hears the reason, in the chat or from the avatar.`}
          label="Reason"
          required
          confirm="Reject"
          danger
          busy={busy}
          onClose={() => setDialog(null)}
          onConfirm={(note) => act(() => api.decide(ref, "rejected", note), `${ref} rejected; the requester hears your reason.`)}
        />
      )}
      {dialog === "cancel" && (
        <TextDialog
          title={`Cancel ${ref}?`}
          intro={`${ticket.title}. The requester hears that it was cancelled, and the Slack card, if there is one, says so.`}
          label="Note (optional)"
          confirm="Cancel request"
          danger
          busy={busy}
          onClose={() => setDialog(null)}
          onConfirm={(note) => act(() => api.cancel(ref, note), `${ref} cancelled.`)}
        />
      )}
      {dialog === "fulfil" && (
        <TextDialog
          title={`Mark ${ref} fulfilled?`}
          intro="For when fulfilment never ran, for example because n8n was down after the approval. The requester hears that it is done."
          label="Note (optional)"
          confirm="Mark fulfilled"
          busy={busy}
          onClose={() => setDialog(null)}
          onConfirm={(note) => act(() => api.fulfil(ref, note), `${ref} marked fulfilled.`)}
        />
      )}
      {dialog === "message" && (
        <TextDialog
          title={`Message the requester of ${ref}`}
          intro="It appears in their conversation, and the avatar speaks it during a voice session."
          label="Message"
          required
          confirm="Send"
          busy={busy}
          onClose={() => setDialog(null)}
          onConfirm={(text) => act(() => api.message(ref, text), `Message sent to the requester of ${ref}.`)}
        />
      )}
      {dialog === "edit" && (
        <EditDialog
          ticket={ticket}
          busy={busy}
          onClose={() => setDialog(null)}
          onConfirm={(changes) => act(() => api.edit(ref, changes), `${ref} updated.`)}
        />
      )}
    </>
  );
}

function TextDialog(props: {
  title: string;
  intro: string;
  label: string;
  confirm: string;
  required?: boolean;
  danger?: boolean;
  busy: boolean;
  onClose: () => void;
  onConfirm: (text: string) => void;
}) {
  const [text, setText] = useState("");
  const ready = !props.required || text.trim().length > 0;
  return (
    <Modal variant="small" isOpen onClose={props.onClose} aria-labelledby="ticket-dialog-title">
      <ModalHeader title={props.title} labelId="ticket-dialog-title" description={props.intro} />
      <ModalBody>
        <Form onSubmit={(e) => e.preventDefault()}>
          <FormGroup label={props.label} isRequired={props.required} fieldId="ticket-dialog-text">
            <TextArea id="ticket-dialog-text" value={text} onChange={(_e, v) => setText(v)} autoFocus resizeOrientation="vertical" />
          </FormGroup>
        </Form>
      </ModalBody>
      <ModalFooter>
        <Button
          variant={props.danger ? "danger" : "primary"}
          onClick={() => props.onConfirm(text.trim())}
          isDisabled={!ready || props.busy}
          isLoading={props.busy}
        >
          {props.confirm}
        </Button>
        <Button variant="link" onClick={props.onClose}>
          Close
        </Button>
      </ModalFooter>
    </Modal>
  );
}

function EditDialog(props: {
  ticket: TicketLike;
  busy: boolean;
  onClose: () => void;
  onConfirm: (changes: { priority?: string; category?: string; note?: string }) => void;
}) {
  const [priority, setPriority] = useState(props.ticket.priority);
  const [category, setCategory] = useState(props.ticket.category ?? "other");
  const [note, setNote] = useState("");
  const changes = {
    ...(priority !== props.ticket.priority ? { priority } : {}),
    ...(category !== (props.ticket.category ?? "other") ? { category } : {}),
  };
  return (
    <Modal variant="small" isOpen onClose={props.onClose} aria-labelledby="edit-dialog-title">
      <ModalHeader
        title={`Edit ${props.ticket.ticket_ref}`}
        labelId="edit-dialog-title"
        description="Recorded on the ticket; the requester is not told."
      />
      <ModalBody>
        <Form onSubmit={(e) => e.preventDefault()}>
          <FormGroup label="Priority" fieldId="edit-priority">
            <FormSelect id="edit-priority" value={priority} onChange={(_e, v) => setPriority(v)}>
              {PRIORITIES.map((p) => (
                <FormSelectOption key={p} value={p} label={p} />
              ))}
            </FormSelect>
          </FormGroup>
          <FormGroup label="Category" fieldId="edit-category">
            <FormSelect id="edit-category" value={category} onChange={(_e, v) => setCategory(v)}>
              {CATEGORIES.map((c) => (
                <FormSelectOption key={c} value={c} label={words(c)} />
              ))}
            </FormSelect>
          </FormGroup>
          <FormGroup label="Note (optional)" fieldId="edit-note">
            <TextArea id="edit-note" value={note} onChange={(_e, v) => setNote(v)} resizeOrientation="vertical" />
          </FormGroup>
        </Form>
      </ModalBody>
      <ModalFooter>
        <Button
          onClick={() => props.onConfirm({ ...changes, ...(note.trim() ? { note: note.trim() } : {}) })}
          isDisabled={Object.keys(changes).length === 0 || props.busy}
          isLoading={props.busy}
        >
          Save
        </Button>
        <Button variant="link" onClick={props.onClose}>
          Close
        </Button>
      </ModalFooter>
    </Modal>
  );
}
