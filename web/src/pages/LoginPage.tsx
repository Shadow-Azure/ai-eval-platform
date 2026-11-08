import { useState, type FormEvent } from "react";
import { Button, Checkbox, Input } from "antd";
import { useNavigate } from "react-router-dom";
import { Icon } from "../components/Icon";

export function LoginPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("admin@acme.local");
  const [password, setPassword] = useState("eval-demo");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNotice(null);

    if (!email.trim() || !password.trim()) {
      setError("请输入账号和密码后重试。");
      return;
    }

    // Demo shell only: credentials are never sent or persisted.
    setError(null);
    navigate("/overview", { replace: true });
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

        <div className="login-footnote">M2 · Control plane foundation · 2026.11</div>
      </section>

      <section className="login-panel">
        <form className="login-form" onSubmit={handleSubmit} noValidate>
          <div className="eyebrow">Welcome back</div>
          <h2>登录评测平台</h2>
          <p>使用组织账号访问工作区。</p>

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
            <span className="hint">演示原型：保留默认值直接登录，或清空密码查看错误态。</span>
          </div>

          <div className="row-between small login-options">
            <Checkbox defaultChecked>保持登录</Checkbox>
            <Button
              type="link"
              size="small"
              className="link-button"
              onClick={() => {
                setError(null);
                setNotice("演示环境不支持找回密码，请使用下方演示账号登录。");
              }}
            >
              忘记密码
            </Button>
          </div>

          <Button type="primary" htmlType="submit" size="large" block className="login-submit">
            <span>进入工作台</span>
            <Icon name="arrowRight" size={15} />
          </Button>

          <div className="demo-account">
            <strong>演示账号</strong>
            <br />
            admin@acme.local · eval-demo
            <br />
            <span className="muted">仅用于演示，凭据不会发送到网络或本地持久化。</span>
          </div>
        </form>
      </section>
    </main>
  );
}
