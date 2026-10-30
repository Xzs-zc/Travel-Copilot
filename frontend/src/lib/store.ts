import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { UserProfile } from "./types";

type TravelState = {
  selectedProvince: { name: string; adcode: string } | null;
  selectedAttraction: { poiId: string; name: string; city?: string; heroImage?: string } | null;
  sessionId: string;
  user: UserProfile | null;
  setProvince: (value: { name: string; adcode: string } | null) => void;
  setAttraction: (value: { poiId: string; name: string; city?: string; heroImage?: string } | null) => void;
  setSessionId: (value: string) => void;
  setUser: (value: UserProfile | null) => void;
};

export const useTravelStore = create<TravelState>()(persist((set) => ({
  selectedProvince: null,
  selectedAttraction: null,
  sessionId: "",
  user: null,
  setProvince: (selectedProvince) => set({ selectedProvince }),
  setAttraction: (selectedAttraction) => set({ selectedAttraction }),
  setSessionId: (sessionId) => set({ sessionId }),
  setUser: (user) => set({ user }),
}), { name: "travel-copilot-state" }));
