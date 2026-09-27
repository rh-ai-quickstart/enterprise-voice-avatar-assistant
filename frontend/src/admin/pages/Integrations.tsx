import {
  Alert,
  Button,
  Card,
  CardBody,
  CardFooter,
  CardHeader,
  CardTitle,
  Content,
  DescriptionList,
  DescriptionListDescription,
  DescriptionListGroup,
  DescriptionListTerm,
  Flex,
  Gallery,
  PageSection,
  Spinner,
  Title,
} from "@patternfly/react-core";
import { CheckCircleIcon } from "@patternfly/react-icons/dist/esm/icons/check-circle-icon";
import { ExclamationCircleIcon } from "@patternfly/react-icons/dist/esm/icons/exclamation-circle-icon";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { IntegrationStateLabel } from "../components/Labels";
import { useToast } from "../components/Toasts";
import { when, words } from "../format";
import type { Integration, IntegrationCheck } from "../types";

const STATES = {
  on: "Enabled, and nothing failed since the last passing test.",
  off: "Disabled in the chart values.",
  misconfigured: "Enabled, but a key it needs is missing.",
  failing: "The last test failed, or a call failed after the last passing test.",
};

export function IntegrationsPage() {
  const list = useQuery({ queryKey: ["integrations"], queryFn: api.integrations });
  return (
    <PageSection>
      <Title headingLevel="h1">Integrations</Title>
      <Content component="p">
        What each integration is set to, without its secrets, and a live test. Errors the workflows and the RAG API meet
        while calling them show here and in the activity feed.
      </Content>
      {list.isPending && <Spinner aria-label="Loading the integrations" />}
      {list.isError && <Alert variant="danger" isInline title={list.error.message} />}
      {list.data && (
        <Gallery hasGutter minWidths={{ default: "320px" }}>
          {list.data.items.map((i) => (
            <IntegrationCard key={i.name} integration={i} />
          ))}
        </Gallery>
      )}
    </PageSection>
  );
}

function value(v: unknown): string {
  if (v === null || v === undefined || v === "") return "-";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "-";
  return typeof v === "string" ? v : JSON.stringify(v);
}

export function Checks({ checks }: { checks: IntegrationCheck[] }) {
  return (
    <div>
      {checks.map((c) => (
        <div key={c.name} className="admin-check">
          {c.ok ? (
            <CheckCircleIcon color="var(--pf-t--global--icon--color--status--success--default)" aria-label="passed" />
          ) : (
            <ExclamationCircleIcon color="var(--pf-t--global--icon--color--status--danger--default)" aria-label="failed" />
          )}
          <span>
            <strong>{c.name}</strong>: {c.detail}
          </span>
        </div>
      ))}
    </div>
  );
}

function IntegrationCard({ integration: i }: { integration: Integration }) {
  const client = useQueryClient();
  const toast = useToast();
  const test = useMutation({
    mutationFn: () => api.testIntegration(i.name),
    onSuccess: (r) => {
      client.invalidateQueries({ queryKey: ["integrations"] });
      client.invalidateQueries({ queryKey: ["overview"] });
      toast(r.ok ? "success" : "danger", `${i.label} test ${r.ok ? "passed" : "failed"}.`);
    },
    onError: (e: Error) => toast("danger", e.message),
  });
  const checks = i.last_test?.data.checks ?? [];
  // An error newer than the last test is why the integration is failing
  const errorSinceTest = i.last_error && (!i.last_test || i.last_error.created_at > i.last_test.created_at);

  return (
    <Card isCompact aria-label={i.label}>
      <CardHeader>
        <Flex justifyContent={{ default: "justifyContentSpaceBetween" }} alignItems={{ default: "alignItemsCenter" }}>
          <CardTitle>{i.label}</CardTitle>
          <span title={STATES[i.state]}>
            <IntegrationStateLabel state={i.state} />
          </span>
        </Flex>
      </CardHeader>
      <CardBody>
        {i.missing.length > 0 &&
          (i.enabled ? (
            <Alert variant="warning" isInline isPlain title={`Missing: ${i.missing.join(", ")}`} />
          ) : (
            <Content component="small">Turning it on also needs {i.missing.join(", ")}.</Content>
          ))}
        <DescriptionList isCompact isHorizontal>
          {Object.entries(i.config).map(([k, v]) => (
            <DescriptionListGroup key={k}>
              <DescriptionListTerm>{words(k)}</DescriptionListTerm>
              <DescriptionListDescription>{value(v)}</DescriptionListDescription>
            </DescriptionListGroup>
          ))}
        </DescriptionList>
        <Title headingLevel="h3" size="md" style={{ marginTop: "var(--pf-t--global--spacer--md)" }}>
          Last test
        </Title>
        {i.last_test ? (
          <>
            <Content component="small">
              {when(i.last_test.created_at)}: {i.last_test.severity === "success" ? "passed" : "failed"}
            </Content>
            <Checks checks={checks} />
          </>
        ) : (
          <Content component="small">Never tested.</Content>
        )}
        {i.last_error && (
          <>
            <Title headingLevel="h3" size="md" style={{ marginTop: "var(--pf-t--global--spacer--md)" }}>
              Last error{errorSinceTest ? "" : " (before the last test)"}
            </Title>
            <Content component="small">
              {when(i.last_error.created_at)}: {i.last_error.title}
            </Content>
            {i.last_error.detail && <div className="admin-timeline__note admin-pre">{i.last_error.detail}</div>}
          </>
        )}
      </CardBody>
      <CardFooter>
        <Button variant="secondary" size="sm" onClick={() => test.mutate()} isLoading={test.isPending} isDisabled={test.isPending}>
          Test
        </Button>
      </CardFooter>
    </Card>
  );
}
