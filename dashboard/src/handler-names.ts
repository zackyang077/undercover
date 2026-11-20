import { createContext } from "react";
import type { Handler } from "./types";

export const HandlerNames = createContext<ReadonlyMap<string, Handler>>(
  new Map(),
);
