import Navigation from '@/components/Navigation';
import ParticleBackground from '@/components/ParticleBackground';
import Hero from '@/components/Hero';
import About from '@/components/About';
import Skills from '@/components/Skills';
import Projects from '@/components/Projects';
import TikTokContent from '@/components/TikTokContent';
import Contact from '@/components/Contact';
import Footer from '@/components/Footer';

export default function Home() {
  return (
    <div className="min-h-screen bg-background text-foreground overflow-hidden">
      <ParticleBackground />
      <Navigation />
      <main className="relative z-10">
        <Hero />
        <About />
        <Skills />
        <Projects />
        <TikTokContent />
        <Contact />
      </main>
      <Footer />
    </div>
  );
}
