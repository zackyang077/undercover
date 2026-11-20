import { useContext } from "react";
import { HandlerNames } from "./handler-names";

export function HandlerIdentity({ id }: Readonly<{ id: string }>) {
  const handler = useContext(HandlerNames).get(id);
  return handler ? (
    <span>
      {handler.name} <small className="handler-id">{handler.handler_id}</small>
    </span>
  ) : (
    <span>{id}</span>
  );
}
