import { UserRound } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useTravelStore } from "../lib/store";

export function AccountButton() {
  const navigate = useNavigate();
  const user = useTravelStore((state) => state.user);
  return (
    <button
      type="button"
      onClick={() => navigate("/account")}
      className="fixed right-5 top-5 z-50 flex items-center gap-2 rounded-full border border-[#f6f1e7]/16 bg-[#07110f]/68 px-4 py-2 text-xs text-[#f6f1e7]/78 shadow-[0_12px_32px_rgba(0,0,0,.24)] backdrop-blur-xl transition hover:border-[#e6b45e]/45 hover:text-[#e6b45e]"
    >
      <UserRound className="h-4 w-4" />
      {user ? user.username : "登录 / 注册"}
    </button>
  );
}
