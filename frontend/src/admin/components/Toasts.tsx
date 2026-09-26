import { Alert, AlertActionCloseButton, AlertGroup } from "@patternfly/react-core";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

type Variant = "success" | "danger" | "warning" | "info";
interface Toast {
  id: number;
  variant: Variant;
  title: string;
}

const ToastContext = createContext<(variant: Variant, title: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const remove = (id: number) => setToasts((all) => all.filter((t) => t.id !== id));
  const notify = useCallback((variant: Variant, title: string) => {
    setToasts((all) => [...all, { id: Date.now() + Math.random(), variant, title }]);
  }, []);
  return (
    <ToastContext.Provider value={notify}>
      {children}
      <AlertGroup isToast isLiveRegion>
        {toasts.map((t) => (
          <Alert
            key={t.id}
            variant={t.variant}
            title={t.title}
            timeout={t.variant === "danger" ? 12000 : 6000}
            onTimeout={() => remove(t.id)}
            actionClose={<AlertActionCloseButton onClose={() => remove(t.id)} />}
          />
        ))}
      </AlertGroup>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
