import { useEffect, useState } from "react";
import { LockKeyhole } from "lucide-react";
import { Button, FormItem, Input, Modal } from "../../../shared/components";

interface AutomationAdminUnlockModalProps {
  open: boolean;
  busy?: boolean;
  title?: string;
  description?: string;
  onClose: () => void;
  onSubmit: (password: string) => Promise<void> | void;
}

export function AutomationAdminUnlockModal({
  open,
  busy = false,
  title = "管理员验证",
  description = "输入管理员密码后才能查看或修改脚本配置。",
  onClose,
  onSubmit,
}: AutomationAdminUnlockModalProps) {
  const [password, setPassword] = useState("");

  useEffect(() => {
    if (!open) {
      setPassword("");
    }
  }, [open]);

  const submit = () => {
    if (!password.trim() || busy) {
      return;
    }
    void onSubmit(password);
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      width="420px"
      closable={!busy}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button onClick={submit} loading={busy} disabled={!password.trim()}>
            <LockKeyhole className="h-4 w-4" />
            解锁
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="text-sm text-[var(--color-text-secondary)]">
          {description}
        </div>
        <FormItem label="管理员密码">
          <Input
            autoFocus
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                submit();
              }
            }}
            placeholder="请输入管理员密码"
            autoComplete="current-password"
            disabled={busy}
          />
        </FormItem>
      </div>
    </Modal>
  );
}
