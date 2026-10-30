import { KeyRound } from "lucide-react";
import type { FrontendConfig } from "../lib/types";

export function ConfigGate({ config }: { config: FrontendConfig }) {
  return (
    <main className="grid min-h-screen place-items-center bg-[#07110f] p-6 text-[#f6f1e7]">
      <section className="glass-panel max-w-xl p-8">
        <KeyRound className="mb-5 h-10 w-10 text-[#e6b45e]" />
        <h1 className="font-serif text-3xl">还需要完成环境配置</h1>
        <p className="mt-3 text-[#a7b8ae]">DeepSeek 和地图密钥缺失时，应用不会回退到模拟数据。请在 <code>Traveling/.env</code> 中配置：</p>
        <ul className="mt-5 space-y-2 font-mono text-sm text-[#e6b45e]">
          {config.missing_required.map((name) => <li key={name}>{name}</li>)}
        </ul>
        <p className="mt-6 text-xs leading-6 text-[#78847f]">保存后重启 FastAPI 服务，并刷新页面。</p>
      </section>
    </main>
  );
}
