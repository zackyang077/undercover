import { label, money } from "./api";
import type { ClaimDetail, Wording } from "./types";

export function Evidence({
  detail,
  wording,
}: Readonly<{
  detail: ClaimDetail;
  wording: Wording;
}>) {
  const { claim, policy, customer, plan } = detail;
  return (
    <section className="panel evidence" aria-label="Claim evidence">
      <div className="section-heading">
        <h2>The claim</h2>
        <span className="eyebrow">{label(claim.claim_type)}</span>
      </div>
      <dl className="facts">
        <div>
          <dt>Incident</dt>
          <dd>{claim.incident_date}</dd>
        </div>
        <div>
          <dt>Location</dt>
          <dd>{claim.incident_location}</dd>
        </div>
        <div>
          <dt>Submitted</dt>
          <dd>{claim.submitted_on}</dd>
        </div>
      </dl>
      <h3>Customer's account</h3>
      <p className="story">{claim.story}</p>
      <h3>Items claimed</h3>
      <ul className="items">
        {claim.items.map((item, i) => (
          <li key={`${String(i)}-${item.description}`}>
            <span>
              {item.description}
              <small>{label(item.category)}</small>
            </span>
            <strong className="mono">{money(item.amount)}</strong>
          </li>
        ))}
      </ul>
      <div className="total">
        <span>Total claimed</span>
        <strong className="mono">{money(claim.amount_claimed)}</strong>
      </div>
      <h3>
        Supporting documents{" "}
        <span className="muted">({claim.documents.length})</span>
      </h3>
      {claim.documents.map((doc, i) => (
        <details key={`${String(i)}-${doc.name}`}>
          <summary>
            {doc.name}
            <small>{label(doc.type)}</small>
          </summary>
          <p className="document">{doc.content}</p>
        </details>
      ))}
      <details>
        <summary>
          Policy and plan{" "}
          <small>
            {policy.policy_id} · {label(policy.plan)}
          </small>
        </summary>
        <dl className="facts">
          <div>
            <dt>Purchased</dt>
            <dd>{policy.purchased_on}</dd>
          </div>
          <div>
            <dt>Status</dt>
            <dd>{label(policy.status)}</dd>
          </div>
          <div>
            <dt>Destination</dt>
            <dd>{policy.trip.destination}</dd>
          </div>
          <div>
            <dt>Trip dates</dt>
            <dd>
              {policy.trip.departs_on} to {policy.trip.returns_on}
            </dd>
          </div>
          <div>
            <dt>Trip cost</dt>
            <dd>{money(policy.trip.trip_cost)}</dd>
          </div>
          <div>
            <dt>Travelers</dt>
            <dd>{policy.travelers.join(", ")}</dd>
          </div>
          <div>
            <dt>Sold through</dt>
            <dd>{policy.sold_through}</dd>
          </div>
          <div>
            <dt>Add-ons</dt>
            <dd>
              {policy.add_ons.length
                ? policy.add_ons.map(label).join(", ")
                : "None"}
            </dd>
          </div>
        </dl>
        <h3>Plan limits</h3>
        <dl className="facts">
          {Object.entries(plan)
            .filter(([key]) => key !== "plan" && key !== "name")
            .map(([key, value]) => (
              <div key={key}>
                <dt>{label(key)}</dt>
                <dd>
                  {typeof value === "number" && !key.endsWith("hours")
                    ? money(value)
                    : value}
                </dd>
              </div>
            ))}
        </dl>
        <p>
          Deductible applies to{" "}
          {detail.deductible_applies_to.map(label).join(" and ")}.
        </p>
        {detail.add_ons.map((addon, i) => (
          <p key={i}>
            <strong>{String(addon.name)}</strong> — {String(addon.effect)}
          </p>
        ))}
      </details>
      <details>
        <summary>
          Customer and claim history <small>{customer.name}</small>
        </summary>
        <p>
          {customer.email}
          <br />
          {customer.phone}
          <br />
          Born {customer.date_of_birth}
          <br />
          {Object.values(customer.address).join(", ")}
        </p>
        {customer.previous_claims.length ? (
          customer.previous_claims.map((old) => (
            <article className="history" key={old.claim_id}>
              <strong className="mono">{old.claim_id}</strong>
              <p>{old.summary}</p>
              <small>
                {old.filed_on} · {label(old.outcome)} · {money(old.amount_paid)}
              </small>
            </article>
          ))
        ) : (
          <p>No previous claims.</p>
        )}
      </details>
      <details>
        <summary>
          Policy wording <small>Version {wording.version}</small>
        </summary>
        <pre className="wording">{wording.content}</pre>
      </details>
    </section>
  );
}
