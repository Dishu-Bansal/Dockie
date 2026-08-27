import { Nav } from "./components/Nav";
import { Hero } from "./components/Hero";
import { Capabilities } from "./components/Capabilities";
import { Features } from "./components/Features";
import { Workflow } from "./components/Workflow";
import { Privacy } from "./components/Privacy";
import { Download } from "./components/Download";
import { Footer } from "./components/Footer";

export function App() {
  return (
    <div className="grain relative min-h-svh bg-ink text-fg">
      <Nav />
      <main>
        <Hero />
        <Capabilities />
        <Features />
        <Workflow />
        <Privacy />
        <Download />
      </main>
      <Footer />
    </div>
  );
}
