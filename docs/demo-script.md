# Demo script

Fifteen minutes, mixed audience. Each step has the talking point for decision makers, the exact action for the presenter, and what the audience should see. Everything below was verified on the demo cluster; the expected answers come from the sample documents in `data/sample-docs/`.

## Before you start (ten minutes before)

- `NS=<project> scripts/demo-preflight.sh -f <your values file>`, run from the repository clone, ends with `PRE-FLIGHT OK`: models Ready, the connectivity test pod green, every n8n webhook registered. It needs `helm` on the PATH and takes about a minute.
- All InferenceServices show READY True: `oc get isvc -n <project>`.
- The sample documents are loaded (`scripts/load-sample-docs.sh`) and `#assistant-ingestion` shows their "done" messages.
- Browser windows ready: **A** the frontend (hard refresh, microphone allowed), **B** n8n Executions, **C** Slack with the five `#assistant-*` channels, **D** the Google Drive transcripts folder, **E** the OpenShift AI dashboard, Models tab, **F** the admin portal (`<frontend URL>/admin`), signed in with your name (the password: `oc extract secret/assistant-admin -n <project> --keys=ADMIN_PASSWORD --to=-`), on the Activity page. A terminal on the bastion or your laptop for two commands.
- Avatar minutes: the Tavus free plan has 25 minutes a month and one stream. Rehearse with `voiceAgent.avatarProvider: none`; switch to `tavus` for the real run.
- Start a **new conversation** in the frontend so memory and notices are clean.

## Steps

| Time | Step | Talking point |
|---|---|---|
| 0:00 | Opening | Employees lose time finding answers in policies and chasing requests. This assistant answers from the company's own documents, with sources, by text or voice, and closes the loop on requests. Everything runs on OpenShift AI in this cluster; only the avatar rendering is a cloud service, and it only ever hears the assistant's replies. |
| 1:00 | 1. The stack | Window E, then a terminal: `oc get pods -n <project>` and `oc get isvc -n <project>`. One Helm chart; the language model, Whisper and BGE-M3 served by vLLM on OpenShift AI, sharing one GPU. |
| 2:00 | 2. Documents in | Terminal, in the repository clone: `NS=<project> scripts/load-sample-docs.sh leave-policy.docx`. Window B shows WF2 running; window C `#assistant-ingestion` shows "leave-policy.docx (documents): done, 7 chunks indexed". Docling parsed the DOCX, chunks were embedded and indexed. |
| 3:30 | 3. Ask in text | Window A, type: **How many days of annual leave do I get?** Expected: "25 days of annual leave per calendar year… [1]" citing `leave-policy.docx`; click the citation chip to show the passage. Every answer is grounded and cites its source. |
| 4:30 | 4. Ask by voice | Pick a face under the avatar frame if you want to show the switch (the voice changes with it), then click **Start voice conversation**. The avatar greets you by the name in the header chip ("Hi Mohamed, welcome…"); the greeting also appears in the chat. Say: **How often must administrator passwords be rotated?** Expected spoken answer: every 90 days, citing `password-policy.md` in the Sources panel. While it is still speaking, say **What about normal users?** to show barge-in; expected: every 180 days. |
| 6:30 | 5. Follow-up with memory | Say: **And what is the minimum length?** Expected: 14 characters. The question only makes sense with the previous one; conversation memory lives in PostgreSQL, shared by text and voice. |
| 7:30 | 6. An invoice arrives | Terminal, in the repository clone: `NS=<project> scripts/load-sample-docs.sh invoice-SKY-2026-0817.pdf`. Window B: WF2 hands the inbox file to WF3; window C `#assistant-documents`: classified as **invoice**, with vendor, invoice number, dates and total extracted to JSON. Documents that are not knowledge are routed, not indexed. |
| 9:00 | 7. A request by voice | Say: **I need a new laptop, mine no longer boots.** Expected: "I've logged your request REQ-… Replace non-functional laptop. It's a hardware request … and needs approval." Window C `#assistant-approvals`: the card with Approve and Reject. Click **Approve**. Within seconds the avatar says: "Good news: your request REQ-… was approved by … and has been fulfilled." The chat shows the same line tagged *update*; `#assistant-tickets` has the fulfilment. Intent detection, a ticket in PostgreSQL, an approval workflow in n8n, and the outcome spoken back in the same session. |
| 10:45 | 7b. The service desk's view | Window F: the Activity feed shows the request as it happened (filed, waiting for approval, approved in Slack by you, fulfilled). Click the reference: the ticket with its timeline and the conversation it came from. Approvals is empty again; Knowledge gaps lists the questions the documents could not answer, grouped by meaning. One place to run the service desk, with or without Slack. |
| 11:30 | 8. The transcript | Click **End voice**, then **Archive transcript** in the header (the terminal alternative is `NS=<project> scripts/archive-transcript.sh`). Window D: a new Google Doc "Assistant transcript …"; window C `#assistant-ingestion`: "Transcript … archived to Google Docs and queued for re-ingestion". Type in window A: **Which request did I file today?** Expected: an answer citing `transcript-….md`. The assistant learns from its own conversations. |
| 13:00 | 9. Under the hood | Window E: the three models with their status and metrics. Optional: the Qdrant dashboard Route with the collection and its vectors. |
| 14:00 | 10. Portability | Show `chart/values-demo-cluster.yaml`: `voiceAgent.avatarProvider`, `models.llm.endpoint`. One value switches the avatar provider or points the LLM at a Models-as-a-Service endpoint; Argo CD (or `helm upgrade`) rolls it out. Data never has to leave the cluster unless you choose it. |
| 15:00 | Close | Questions. |

## Without Slack or Google Docs

The same fifteen minutes work with `integrations.slack.enabled` and `integrations.googleDocs.enabled` off; the admin portal (window F) takes the place of windows C and D.

- **Step 2 and step 6.** The results appear in window F: the Activity feed shows "leave-policy.docx indexed (7 chunks)" and the invoice's classification; Documents > Classified shows the invoice's type, summary and extracted fields.
- **Step 7.** Open **Approvals** in window F before speaking: the request appears within a second and the badge counts it. Click **Approve**. The avatar says "…was approved by <your name> and has been fulfilled." Reject with a reason to show that the reason is spoken too.
- **Step 8.** **Archive transcript** stores the transcript in the `transcripts` bucket and re-indexes it; there is no Google Doc. Window F, Conversations: the conversation shows the archive with a **Download** link. The follow-up question still cites `transcript-….md`.

## Expected answers at a glance

| Question | Answer | Source |
|---|---|---|
| How many days of annual leave do I get? | 25 days, plus 1 per five years up to 30 | leave-policy.docx |
| How often must administrator passwords be rotated? | Every 90 days (standard users 180) | password-policy.md |
| What is the minimum password length? | 14 characters | password-policy.md |
| What is the laptop replacement cycle? | 36 months | it-equipment-procedure.docx |
| What is the response time for a Sev 1 incident? | 15 minutes, resolution 4 hours | incident-response-procedure.pdf |
| What is the hotel rate cap outside the head office city? | EUR 190 per night | expense-reimbursement-policy.pdf |
| What happens after ten failed sign-in attempts? | The account locks for 30 minutes | password-policy.md |

## If something goes wrong

- The avatar does not join within twenty seconds: click End voice, then Start again (a new room gets a new agent). See `docs/troubleshooting.md`.
- Approval card without buttons or an outcome that is not spoken: the request must be filed in the *same* session that is live in the browser; the notice goes to the conversation that filed it.
- A click on the Slack card does nothing: approve in window F instead; the card is updated with the decision all the same. The Integrations page there tests Slack and shows the last error (a missing signing secret is the usual cause).
- Keep the text path as the fallback for every voice step; the answers and citations are identical.
