export function RemotePanel({
  page,
  error,
  retry,
}: Readonly<{ page: string; error: string; retry: () => void }>) {
  return (
    <section className="panel">
      {error ? (
        <>
          <p role="alert">{error}</p>
          <button className="secondary" onClick={retry}>
            Reload {page}
          </button>
        </>
      ) : (
        <p role="status">Loading {page}…</p>
      )}
    </section>
  );
}
