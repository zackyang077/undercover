import { useEffect, useState } from "react";
import { api, message } from "./api";

export function usePageData<T>(path: string) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    void api<T>(path)
      .then((value) => {
        if (active) setData(value);
      })
      .catch((failure: unknown) => {
        if (active) setError(message(failure));
      });
    return () => {
      active = false;
    };
  }, [path, attempt]);
  function retry() {
    setError("");
    setAttempt((n) => n + 1);
  }
  return { data, setData, error, retry };
}
