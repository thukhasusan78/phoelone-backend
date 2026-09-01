import { useEffect, useState } from 'react';

const skills = [
  { name: 'Python', category: 'Language' },
  { name: 'FastAPI', category: 'Framework' },
  { name: 'Google Gemini AI', category: 'AI' },
  { name: 'LLMs', category: 'AI' },
  { name: 'Telegram Bot Development', category: 'Automation' },
  { name: 'Playwright', category: 'Automation' },
  { name: 'LanceDB', category: 'Database' },
  { name: 'Face Recognition', category: 'Computer Vision' },
  { name: 'Edge TTS', category: 'Voice' },
  { name: 'Web Scraping', category: 'Tools' },
  { name: 'SSH', category: 'Tools' },
  { name: 'Cloudflare Tunnels', category: 'Infrastructure' },
  { name: 'APScheduler', category: 'Tools' },
];

const categories = ['Language', 'Framework', 'AI', 'Automation', 'Database', 'Computer Vision', 'Voice', 'Tools', 'Infrastructure'];
const categoryColors: Record<string, string> = {
  Language: 'from-blue-500 to-cyan-500',
  Framework: 'from-purple-500 to-pink-500',
  AI: 'from-orange-500 to-red-500',
  Automation: 'from-green-500 to-emerald-500',
  Database: 'from-indigo-500 to-blue-500',
  'Computer Vision': 'from-pink-500 to-rose-500',
  Voice: 'from-yellow-500 to-orange-500',
  Tools: 'from-cyan-500 to-blue-500',
  Infrastructure: 'from-violet-500 to-purple-500',
};

export default function Skills() {
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

    const section = document.getElementById('skills');
    if (section) observer.observe(section);

    return () => observer.disconnect();
  }, []);

  return (
    <section id="skills" className="py-20 relative z-10">
      <div className="container mx-auto px-4">
        <div className="max-w-6xl mx-auto">
          {/* Section title */}
          <div className={`mb-16 transition-all duration-1000 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-10'}`}>
            <h2 className="text-4xl md:text-5xl font-bold glow-text mb-4">Skills & Technologies</h2>
            <div className="w-20 h-1 bg-gradient-to-r from-primary to-secondary rounded-full" />
          </div>

          {/* Skills grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {skills.map((skill, index) => {
              const gradientClass = categoryColors[skill.category] || 'from-cyan-500 to-blue-500';
              return (
                <div
                  key={index}
                  className={`relative group transition-all duration-500 transform ${
                    isVisible
                      ? 'opacity-100 translate-y-0'
                      : 'opacity-0 translate-y-10'
                  }`}
                  style={{
                    transitionDelay: isVisible ? `${index * 30}ms` : '0ms',
                  }}
                >
                  <div className={`absolute inset-0 bg-gradient-to-r ${gradientClass} rounded-lg opacity-0 group-hover:opacity-100 transition-opacity duration-300 blur-md`} />
                  <div className="relative p-4 rounded-lg border border-border bg-card/80 backdrop-blur-sm hover:border-primary transition-all duration-300 group-hover:scale-105">
                    <h3 className="text-lg font-bold text-foreground mb-1">
                      {skill.name}
                    </h3>
                    <p className="text-sm text-muted-foreground">
                      {skill.category}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Category legend */}
          <div className={`mt-16 p-8 rounded-lg border border-border bg-card/50 backdrop-blur-sm transition-all duration-1000 ${
            isVisible ? 'opacity-100' : 'opacity-0'
          }`}>
            <h3 className="text-lg font-bold text-foreground mb-4">Technology Categories</h3>
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
              {categories.map((category) => (
                <div key={category} className="flex items-center gap-2">
                  <div className={`w-3 h-3 rounded-full bg-gradient-to-r ${categoryColors[category]}`} />
                  <span className="text-sm text-foreground/70">{category}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
