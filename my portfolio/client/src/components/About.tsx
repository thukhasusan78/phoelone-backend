import { useEffect, useState } from 'react';
import { Brain, Zap, Film, Globe } from 'lucide-react';

const specializations = [
  {
    icon: Brain,
    title: 'AI Agent Development',
    description: 'Building sophisticated AI agents with advanced reasoning, memory systems, and autonomous decision-making capabilities.',
  },
  {
    icon: Zap,
    title: 'Telegram Automation',
    description: 'Creating powerful Telegram bots and userbots for automation, content distribution, and intelligent interactions.',
  },
  {
    icon: Film,
    title: 'Media Processing',
    description: 'Advanced media handling including face recognition, video processing, and intelligent content classification.',
  },
  {
    icon: Globe,
    title: 'Web Development',
    description: 'Full-stack web development with modern frameworks, creating responsive and performant applications.',
  },
];

export default function About() {
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
        }
      },
      { threshold: 0.1 }
    );

    const section = document.getElementById('about');
    if (section) observer.observe(section);

    return () => observer.disconnect();
  }, []);

  return (
    <section id="about" className="py-20 relative z-10">
      <div className="container mx-auto px-4">
        <div className="max-w-4xl mx-auto">
          {/* Section title */}
          <div className={`mb-16 transition-all duration-1000 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-10'}`}>
            <h2 className="text-4xl md:text-5xl font-bold glow-text mb-4">About Me</h2>
            <div className="w-20 h-1 bg-gradient-to-r from-primary to-secondary rounded-full" />
          </div>

          {/* Specializations grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            {specializations.map((spec, index) => {
              const Icon = spec.icon;
              return (
                <div
                  key={index}
                  className={`p-6 rounded-lg border border-border bg-card/50 backdrop-blur-sm hover:border-primary transition-all duration-500 transform hover:scale-105 ${
                    isVisible
                      ? 'opacity-100 translate-y-0'
                      : 'opacity-0 translate-y-10'
                  }`}
                  style={{
                    transitionDelay: isVisible ? `${index * 100}ms` : '0ms',
                  }}
                >
                  <Icon className="w-12 h-12 text-primary mb-4" />
                  <h3 className="text-xl font-bold text-foreground mb-3">
                    {spec.title}
                  </h3>
                  <p className="text-foreground/70">
                    {spec.description}
                  </p>
                </div>
              );
            })}
          </div>

          {/* Additional info */}
          <div className={`mt-16 p-8 rounded-lg border border-primary/30 bg-gradient-to-r from-primary/5 to-secondary/5 transition-all duration-1000 ${
            isVisible ? 'opacity-100' : 'opacity-0'
          }`}>
            <p className="text-lg text-foreground leading-relaxed">
              With expertise in Python, FastAPI, Google Gemini AI, and modern web technologies,
              I create intelligent solutions that combine cutting-edge AI with practical automation.
              My passion lies in building systems that are not just powerful, but also elegant and efficient.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
