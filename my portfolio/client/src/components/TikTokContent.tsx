import { useEffect, useState } from 'react';
import { Zap, Smartphone, Brain, Cpu, BookOpen, ShoppingCart, Loader } from 'lucide-react';
import { trpc } from '@/lib/trpc';

const tiktokContent = [
  {
    icon: Zap,
    title: 'DIY Bluetooth Jammers',
    description: 'Custom-built Bluetooth jammers with OLED displays. Builds and sells through TikTok orders.',
    color: 'from-yellow-500 to-orange-500',
  },
  {
    icon: Smartphone,
    title: 'Portable Power Stations',
    description: 'DIY assembly tutorials and reviews for portable power solutions.',
    color: 'from-green-500 to-emerald-500',
  },
  {
    icon: Brain,
    title: 'Personal AI (Jarvis Model)',
    description: 'Development and demonstrations of advanced AI agent systems.',
    color: 'from-purple-500 to-pink-500',
  },
  {
    icon: Cpu,
    title: 'Robotics & Emo Robot',
    description: 'Reviews, demos, and tutorials for robotics projects and Emo robot interactions.',
    color: 'from-blue-500 to-cyan-500',
  },
  {
    icon: BookOpen,
    title: 'Tech Solutions & Tutorials',
    description: 'Telegram auto-selling systems, Bluetooth device connections, and tech tips.',
    color: 'from-indigo-500 to-blue-500',
  },
  {
    icon: ShoppingCart,
    title: 'Custom Gadgets Store',
    description: 'Sells custom-built gadgets and devices directly through TikTok orders.',
    color: 'from-red-500 to-pink-500',
  },
];

export default function TikTokContent() {
  const [isVisible, setIsVisible] = useState(false);
  const { data: tiktokStats } = trpc.tiktok.stats.useQuery();

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
        }
      },
      { threshold: 0.1 }
    );

    const section = document.getElementById('tiktok-content');
    if (section) observer.observe(section);

    return () => observer.disconnect();
  }, []);

  return (
    <section id="tiktok-content" className="py-20 relative z-10">
      <div className="container mx-auto px-4">
        <div className="max-w-6xl mx-auto">
          {/* Section title */}
          <div className={`mb-16 transition-all duration-1000 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-10'}`}>
            <h2 className="text-4xl md:text-5xl font-bold glow-text mb-4">TikTok Content</h2>
            <div className="w-20 h-1 bg-gradient-to-r from-primary to-secondary rounded-full mb-4" />
            <p className="text-lg text-foreground/70">
              THUKHA Industries (@thukhatech) - Creating tech content in Burmese for the Myanmar community
            </p>
          </div>

          {/* Content grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-12">
            {tiktokContent.map((content, index) => {
              const Icon = content.icon;
              return (
                <div
                  key={index}
                  className={`relative group transition-all duration-500 transform ${
                    isVisible
                      ? 'opacity-100 translate-y-0'
                      : 'opacity-0 translate-y-10'
                  }`}
                  style={{
                    transitionDelay: isVisible ? `${index * 50}ms` : '0ms',
                  }}
                >
                  <div className={`absolute inset-0 bg-gradient-to-r ${content.color} rounded-lg opacity-0 group-hover:opacity-100 transition-opacity duration-300 blur-md`} />
                  <div className="relative p-6 rounded-lg border border-border bg-card/80 backdrop-blur-sm hover:border-primary transition-all duration-300 group-hover:scale-105 h-full flex flex-col">
                    <Icon className="w-10 h-10 text-primary mb-4" />
                    <h3 className="text-lg font-bold text-foreground mb-3">
                      {content.title}
                    </h3>
                    <p className="text-foreground/70 text-sm flex-1">
                      {content.description}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Stats and CTA */}
          <div className={`p-8 rounded-lg border border-primary/30 bg-gradient-to-r from-primary/5 to-secondary/5 transition-all duration-1000 ${
            isVisible ? 'opacity-100' : 'opacity-0'
          }`}>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
              <div className="text-center">
                {tiktokStats ? (
                  <>
                    <div className="text-3xl font-bold text-primary mb-1">{tiktokStats.formatted.followers}</div>
                    <div className="text-foreground/70">Followers</div>
                  </>
                ) : (
                  <div className="flex items-center justify-center gap-2">
                    <Loader size={20} className="animate-spin text-primary" />
                  </div>
                )}
              </div>
              <div className="text-center">
                {tiktokStats ? (
                  <>
                    <div className="text-3xl font-bold text-primary mb-1">{tiktokStats.formatted.likes}</div>
                    <div className="text-foreground/70">Likes</div>
                  </>
                ) : (
                  <div className="flex items-center justify-center gap-2">
                    <Loader size={20} className="animate-spin text-primary" />
                  </div>
                )}
              </div>
              <div className="text-center">
                <div className="text-3xl font-bold text-primary mb-1">🇲🇲</div>
                <div className="text-foreground/70">Myanmar Community</div>
              </div>
            </div>
            <p className="text-foreground/70 text-center mb-4">
              All content created in Burmese language, featuring DIY tech projects, gadget reviews, and custom electronics.
            </p>
            <div className="flex justify-center">
              <a
                href="https://www.tiktok.com/@thukhatech"
                target="_blank"
                rel="noopener noreferrer"
                className="neon-button"
              >
                Follow on TikTok
              </a>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
