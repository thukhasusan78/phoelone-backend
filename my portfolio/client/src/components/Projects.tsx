import { useEffect, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import ProjectGallery from './ProjectGallery';

const jarvisFeatures = [
  {
    title: 'Google Gemini AI Brain',
    description: 'Advanced AI reasoning engine powering intelligent decision-making and natural conversations.',
  },
  {
    title: 'FastAPI + Uvicorn Server',
    description: 'High-performance async server architecture for handling concurrent requests efficiently.',
  },
  {
    title: 'Telegram Bot + Userbot',
    description: 'Dual interface for both bot commands and userbot automation capabilities.',
  },
  {
    title: 'Playwright Browser Automation',
    description: 'Automated web browsing and interaction for data collection and task automation.',
  },
  {
    title: 'LanceDB Vector Database',
    description: 'Efficient vector storage for semantic search and similarity matching.',
  },
  {
    title: 'Edge TTS with Myanmar Voice',
    description: 'Text-to-speech synthesis with native Myanmar language support for voice interactions.',
  },
  {
    title: 'Face Recognition',
    description: 'Advanced computer vision for facial detection and recognition tasks.',
  },
  {
    title: 'Movie Engine',
    description: 'Intelligent system for auto-detecting, classifying, deduplicating, and reposting movies to Telegram channels.',
  },
  {
    title: 'Web Search Tool',
    description: 'Real-time internet search integration for current information retrieval.',
  },
  {
    title: 'File Management',
    description: 'Comprehensive file handling and organization capabilities.',
  },
  {
    title: 'System Commands',
    description: 'Execute system-level operations and scripts.',
  },
  {
    title: 'Hybrid Memory System',
    description: 'Dual-layer memory: Short-term SQLite for immediate context and Long-term Vector DB for historical knowledge.',
  },
];

export default function Projects() {
  const [isVisible, setIsVisible] = useState(false);
  const [expandedFeatures, setExpandedFeatures] = useState<number[]>([]);

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
        }
      },
      { threshold: 0.1 }
    );

    const section = document.getElementById('projects');
    if (section) observer.observe(section);

    return () => observer.disconnect();
  }, []);

  const toggleFeature = (index: number) => {
    setExpandedFeatures((prev) =>
      prev.includes(index)
        ? prev.filter((i) => i !== index)
        : [...prev, index]
    );
  };

  return (
    <section id="projects" className="py-20 relative z-10">
      <div className="container mx-auto px-4">
        <div className="max-w-5xl mx-auto">
          {/* Section title */}
          <div className={`mb-16 transition-all duration-1000 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-10'}`}>
            <h2 className="text-4xl md:text-5xl font-bold glow-text mb-4">Featured Project</h2>
            <div className="w-20 h-1 bg-gradient-to-r from-primary to-secondary rounded-full" />
          </div>

          {/* Jarvis AI Agent Project */}
          <div className={`rounded-lg border border-primary/50 bg-gradient-to-br from-card to-card/50 backdrop-blur-sm p-8 md:p-12 transition-all duration-1000 ${
            isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-10'
          }`}>
            {/* Project header */}
            <div className="mb-8">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-4 h-4 rounded-full bg-primary animate-pulse" />
                <span className="text-sm font-semibold text-primary uppercase tracking-wider">Flagship Project</span>
              </div>
              <h3 className="text-3xl md:text-4xl font-bold text-foreground mb-3">
                Jarvis AI Agent
              </h3>
              <p className="text-lg text-foreground/70">
                A sophisticated AI agent system built with Python, combining cutting-edge AI capabilities with practical automation tools.
              </p>
            </div>

            {/* Features grid */}
            <div className="space-y-3">
              {jarvisFeatures.map((feature, index) => (
                <div
                  key={index}
                  className={`border border-border rounded-lg overflow-hidden transition-all duration-300 ${
                    expandedFeatures.includes(index)
                      ? 'border-primary bg-primary/5'
                      : 'hover:border-primary/50'
                  }`}
                >
                  <button
                    onClick={() => toggleFeature(index)}
                    className="w-full p-4 flex items-center justify-between text-left hover:bg-primary/5 transition-colors duration-200"
                  >
                    <div>
                      <h4 className="font-semibold text-foreground">
                        {feature.title}
                      </h4>
                    </div>
                    <ChevronDown
                      size={20}
                      className={`text-primary transition-transform duration-300 flex-shrink-0 ${
                        expandedFeatures.includes(index) ? 'rotate-180' : ''
                      }`}
                    />
                  </button>
                  {expandedFeatures.includes(index) && (
                    <div className="px-4 pb-4 text-foreground/70 border-t border-border">
                      {feature.description}
                    </div>
                  )}
                </div>
              ))}
            </div>

            {/* Project stats */}
            <div className="mt-12 grid grid-cols-2 md:grid-cols-4 gap-4">
              {[
                { label: 'Components', value: '12+' },
                { label: 'AI Models', value: 'Gemini' },
                { label: 'Platforms', value: 'Telegram' },
                { label: 'Languages', value: 'Python' },
              ].map((stat, index) => (
                <div
                  key={index}
                  className="p-4 rounded-lg border border-border bg-background/50 text-center"
                >
                  <div className="text-2xl font-bold text-primary mb-1">
                    {stat.value}
                  </div>
                  <div className="text-sm text-foreground/60">
                    {stat.label}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Call to action */}
          <div className={`mt-12 p-8 rounded-lg border border-border bg-card/50 backdrop-blur-sm text-center transition-all duration-1000 ${
            isVisible ? 'opacity-100' : 'opacity-0'
          }`}>
            <p className="text-foreground/70 mb-4">
              Interested in learning more about my projects or collaborating?
            </p>
            <a
              href="#contact"
              className="inline-block neon-button"
            >
              Get in Touch
            </a>
          </div>
        </div>
      </div>

      {/* Project Gallery */}
      <ProjectGallery />
    </section>
  );
}
