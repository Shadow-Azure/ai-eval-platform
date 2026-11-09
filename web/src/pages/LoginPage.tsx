import { useState, type FormEvent } from "react";
import { Button, Checkbox, Input } from "antd";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError } from "../api/types";
import { safeNext, useAuth } from "../app/auth";
import { Icon } from "../components/Icon";

export function LoginPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { status, login } = useAuth();
  const next = safeNext(searchParams.get("next"));

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (status === "authenticated") {
    return <Navigate to={next} replace />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNotice(null);

    if (!email.trim() || !password.trim()) {
      setError("请输入账号和密码后重试。");
      return;
    }

    setError(null);
    setSubmitting(true);
    try {
      await login(email.trim(), password, remember);
      navigate(next, { replace: true });
    } catch (caught) {
      if (caught instanceof ApiError) {
        if (caught.code === "invalid_credentials" || caught.status === 401) {
          setError("账号或密码不正确，请检查后重试。");
        } else if (caught.code === "network_error") {
          setError(caught.message);
        } else {
          setError(caught.message || "登录失败，请稍后重试。");
        }
      } else {
        setError("登录失败，请稍后重试。");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-visual" aria-label="Eval Platform 介绍">
        <div className="login-brand">
          <div className="brand-mark" aria-hidden="true">
            EP
          </div>
          <span>Eval Platform</span>
        </div>

        <div className="login-message">
          <div className="login-kicker">Evaluation infrastructure for coding agents</div>
          <h1>
            把 Agent 能力，
            <br />
            变成可解释的工程指标。
          </h1>
          <p>
            统一管理数据集、模型与执行资源，在真实代码库中运行可复现的 Agent
            评测，并从每一次会话轨迹定位结果背后的原因。
          </p>
          <div className="login-flow" aria-label="平台流程">
            <span>评测集</span>
            <Icon name="arrowRight" size={12} />
            <span>Agent × 资产 × Model</span>
            <Icon name="arrowRight" size={12} />
            <span>资源池</span>
            <Icon name="arrowRight" size={12} />
            <span>结果与轨迹</span>
          </div>
        </div>

        <div className="login-footnote">M3 · Authenticated control plane · 2026.11</div>
      </section>

      <section className="login-panel">
        <form className="login-form" onSubmit={handleSubmit} noValidate>
          <div className="eyebrow">Welcome back</div>
          <h2>登录评测平台</h2>
          <p>使用组织管理员分配的账号访问工作区。</p>

          {error ? (
            <div className="login-error banner danger" role="alert">
              <Icon name="alert" size={15} />
              <span>{error}</span>
            </div>
          ) : null}

          {notice ? (
            <div className="login-error banner info" role="status">
              <Icon name="help" size={15} />
              <span>{notice}</span>
            </div>
          ) : null}

          <div className="field">
            <label htmlFor="login-email" className="required">
              账号
            </label>
            <Input
              id="login-email"
              name="email"
              type="email"
              autoComplete="username"
              autoFocus
              value={email}
              status={error && !email.trim() ? "error" : undefined}
              onChange={(event) => {
                setEmail(event.target.value);
                if (error) setError(null);
              }}
            />
          </div>

          <div className="field">
            <label htmlFor="login-password" className="required">
              密码
            </label>
            <Input.Password
              id="login-password"
              name="password"
              autoComplete="current-password"
              value={password}
              status={error && !password.trim() ? "error" : undefined}
              onChange={(event) => {
                setPassword(event.target.value);
                if (error) setError(null);
              }}
            />
          </div>

          <div className="row-between small login-options">
            <Checkbox
              checked={remember}
              onChange={(event) => setRemember(event.target.checked)}
            >
              保持登录
            </Checkbox>
            <Button
              type="link"
              size="small"
              className="link-button"
              onClick={() => {
                setError(null);
                setNotice("如需重置密码，请联系平台管理员。");
              }}
            >
              忘记密码
            </Button>
          </div>

          <Button
            type="primary"
            htmlType="submit"
            size="large"
            block
            className="login-submit"
            loading={submitting}
          >
            <span>{submitting ? "登录中…" : "进入工作台"}</span>
            <Icon name="arrowRight" size={15} />
          </Button>
        </form>
      </section>
    </main>
  );
}
