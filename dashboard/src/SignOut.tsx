import { signOut } from "./auth";
import { message } from "./api";

export function SignOut({
  onError,
}: Readonly<{ onError: (failure: string) => void }>) {
  return (
    <button
      className="secondary"
      onClick={() => {
        void signOut().catch((failure: unknown) => {
          onError(message(failure));
        });
      }}
    >
      Sign out
    </button>
  );
}
