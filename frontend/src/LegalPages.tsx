import { useEffect } from 'react'
import type { ReactNode } from 'react'

// Replace with the official service contact email before publishing.
const CONTACT_EMAIL = 'charoukoussama52@gmail.com'

const legalLinks = [
  { path: '/privacy-policy', title: 'Privacy Policy' },
  { path: '/terms-of-service', title: 'Terms of Service' },
  { path: '/data-deletion', title: 'Data Deletion' },
]

const linkClass = 'rounded-sm underline underline-offset-4 hover:text-slate-900 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-slate-700'

export function LegalFooter() {
  return (
    <footer className="border-t border-slate-200 bg-white px-5 py-5 text-sm text-slate-600">
      <nav aria-label="Legal information" className="mx-auto flex max-w-3xl flex-wrap justify-center gap-x-6 gap-y-3">
        {legalLinks.map(({ path, title }) => (
          <a key={path} href={path} className={linkClass} aria-current={window.location.pathname.replace(/\/+$/, '') === path ? 'page' : undefined}>
            {title}
          </a>
        ))}
      </nav>
    </footer>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
      {children}
    </section>
  )
}

function Contact() {
  return <p>Contact AI Automation Lab at <span className="break-words font-medium">{CONTACT_EMAIL}</span>.</p>
}

function PrivacyPolicy() {
  return (
    <>
      <Section title="About the service">
        <p>AI Automation Lab provides WhatsApp AI Commerce, a service designed to help customers communicate with a business through WhatsApp, ask about products, and place and manage orders. This policy describes information processed in connection with those interactions.</p>
      </Section>
      <Section title="Information we process">
        <ul className="list-disc space-y-2 pl-6">
          <li>Information you provide in WhatsApp conversations, such as your name, questions, preferences, and customer support requests.</li>
          <li>Your WhatsApp phone number and identifiers associated with your messages, along with contact information such as an email address if you provide it.</li>
          <li>Conversation and message records, including message content, timestamps, message direction, and related status or technical information.</li>
          <li>When you place an order: products, quantities, prices, order and payment status, recipient name, delivery phone number, delivery address, and order or delivery notes.</li>
        </ul>
        <p>The application stores customer, conversation, message, and order records. Please share only information needed for your inquiry or order, and do not send passwords or payment card details in a conversation.</p>
      </Section>
      <Section title="How information is used">
        <p>Information may be used to respond to customers, provide product assistance, process and follow up on orders, coordinate delivery, provide customer support, and operate and troubleshoot the service.</p>
      </Section>
      <Section title="Third-party services">
        <p>WhatsApp messaging uses Meta’s WhatsApp Business Cloud API. Meta processes information under its own terms and privacy policies. Hosting and database infrastructure may also process information needed to run the service. Where delivery is arranged, relevant recipient and delivery details may be shared with the parties fulfilling that order.</p>
        <p>If AI-assisted features are enabled using an external provider, relevant conversation information may be processed by that provider to produce responses.</p>
      </Section>
      <Section title="Security and retention">
        <p>Reasonable safeguards are important to protecting information, but no online service, transmission, or storage system can be guaranteed completely secure.</p>
        <p>Retention depends on the purpose of the records, ongoing orders or support requests, and applicable legal or operational requirements. Some records may need to be retained to resolve disputes or maintain necessary transaction records. This policy does not set a fixed retention period.</p>
      </Section>
      <Section title="Requests about your information">
        <p>You can contact us to request access to, correction of, or deletion of information associated with your interactions. We may ask for limited information to verify your connection to the relevant records. Requests are reviewed subject to applicable requirements.</p>
        <p>See <a className={linkClass} href="/data-deletion">Data Deletion</a> for instructions on requesting deletion.</p>
      </Section>
      <Section title="Contact and related terms">
        <Contact />
        <p>Please also read our <a className={linkClass} href="/terms-of-service">Terms of Service</a>.</p>
      </Section>
    </>
  )
}

function TermsOfService() {
  return (
    <>
      <Section title="Purpose of the service">
        <p>AI Automation Lab’s WhatsApp AI Commerce supports customer conversations, product inquiries, order processing, and customer support through WhatsApp. These terms apply to your use of the service.</p>
      </Section>
      <Section title="Acceptable use">
        <p>Use the service lawfully and respectfully. Do not send harmful or abusive content, impersonate others, submit fraudulent orders, attempt unauthorized access, or disrupt the service. Provide accurate information needed to respond to your requests and fulfill orders. Access may be restricted when necessary to address misuse.</p>
      </Section>
      <Section title="Product and order information">
        <p>Product descriptions, prices, availability, and delivery estimates may change or contain errors. Verify the items, total price, payment arrangements, and delivery details with the business before confirming an order. An automated reply alone does not guarantee stock availability or acceptance of an order. Ask the business about any applicable cancellation, return, or refund conditions.</p>
      </Section>
      <Section title="Automated and AI-generated responses">
        <p>The service may use automated or AI-generated responses. These can be incomplete or incorrect. Verify important information, especially product details, prices, order confirmations, and delivery arrangements, with the business before relying on it.</p>
      </Section>
      <Section title="Availability and third-party services">
        <p>The service may be interrupted for maintenance, technical problems, or other operational reasons. Continuous availability and immediate responses are not guaranteed.</p>
        <p>Messaging depends on WhatsApp and Meta services, and the application may rely on hosting or other service providers. These services have their own terms and may experience changes or outages that affect your interactions.</p>
      </Section>
      <Section title="Service limitations">
        <p>We aim to provide useful assistance, but cannot guarantee that every response is accurate or that the service will always be error-free. Responsibility for problems depends on the circumstances and applicable law. Nothing in these terms excludes rights or responsibilities that cannot lawfully be excluded.</p>
      </Section>
      <Section title="Changes to these terms">
        <p>These terms may be updated as the service changes. Updated terms will appear on this page with a revised last updated date. Please review them periodically.</p>
      </Section>
      <Section title="Contact">
        <Contact />
        <p>For information about personal data, read our <a className={linkClass} href="/privacy-policy">Privacy Policy</a> and <a className={linkClass} href="/data-deletion">Data Deletion instructions</a>.</p>
      </Section>
    </>
  )
}

function DataDeletion() {
  return (
    <>
      <Section title="Request deletion of your information">
        <p>You can request deletion of data associated with your interactions with AI Automation Lab’s WhatsApp AI Commerce service by emailing <span className="break-words font-medium">{CONTACT_EMAIL}</span> with the subject “Data deletion request”.</p>
      </Section>
      <Section title="What to include">
        <ul className="list-disc space-y-2 pl-6">
          <li>The WhatsApp phone number you used, including its country code.</li>
          <li>The name you used in the conversation or order, if provided.</li>
          <li>An approximate conversation date or order reference, if available, to help locate the records.</li>
          <li>Whether your request concerns all associated information or a particular conversation or order.</li>
        </ul>
        <p>Provide only the details needed to identify your records. Do not send passwords, verification codes, payment card details, or identity documents with your initial request.</p>
      </Section>
      <Section title="How requests are handled">
        <p>We will review your request and may ask for limited additional information to verify that the records relate to you. Requests will be handled subject to applicable legal and operational retention requirements, including those related to ongoing orders, disputes, and necessary transaction records. We will explain any relevant limitations when responding.</p>
        <p>This request concerns records held by this service. It does not itself remove messages from your device or records independently held by WhatsApp, Meta, or other parties; their own processes apply to those records.</p>
      </Section>
      <Section title="More information">
        <Contact />
        <p>Read our <a className={linkClass} href="/privacy-policy">Privacy Policy</a> and <a className={linkClass} href="/terms-of-service">Terms of Service</a>.</p>
      </Section>
    </>
  )
}

export function LegalPage({ path }: { path: string }) {
  const title = legalLinks.find((link) => link.path === path)!.title

  useEffect(() => {
    const previousTitle = document.title
    const description = document.querySelector('meta[name="description"]')
    const previousDescription = description?.getAttribute('content') ?? ''
    document.title = `${title} | AI Automation Lab`
    description?.setAttribute('content', `${title} for AI Automation Lab’s WhatsApp AI Commerce service.`)
    return () => {
      document.title = previousTitle
      description?.setAttribute('content', previousDescription)
    }
  }, [title])

  return (
    <div className="flex min-h-screen flex-col bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white px-5 py-5">
        <div className="mx-auto max-w-3xl">
          <p className="text-lg font-semibold tracking-tight">AI Automation Lab</p>
          <p className="mt-1 text-sm text-slate-600">WhatsApp AI Commerce</p>
        </div>
      </header>
      <main className="mx-auto w-full max-w-3xl flex-1 px-5 py-10 sm:py-14">
        <h1 className="text-3xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-3 text-sm text-slate-600">Last updated: <time dateTime="2026-09-23">September 23, 2026</time></p>
        <div className="mt-10 space-y-8 text-base leading-7 text-slate-700">
          {path === '/privacy-policy' ? <PrivacyPolicy /> : path === '/terms-of-service' ? <TermsOfService /> : <DataDeletion />}
        </div>
      </main>
      <LegalFooter />
    </div>
  )
}

export function isLegalPath(path: string) {
  return legalLinks.some((link) => link.path === path)
}
