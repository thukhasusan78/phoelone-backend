import { useEffect, useState } from 'react';
import { Mail, Phone, Github, Send, Heart, Users } from 'lucide-react';

const contactLinks = [
  {
    icon: Send,
    label: 'Telegram',
    handle: '@thukhasusan78',
    url: 'https://t.me/thukhasusan78',
    color: 'from-blue-500 to-cyan-500',
  },
  {
    icon: Phone,
    label: 'Phone',
    handle: '09784679389',
    url: 'tel:09784679389',
    color: 'from-green-500 to-emerald-500',
  },
  {
    icon: Github,
    label: 'GitHub',
    handle: 'github.com/thukhasusan78',
    url: 'https://github.com/thukhasusan78',
    color: 'from-purple-500 to-pink-500',
  },
];

const socialLinks = [
  {
    icon: Heart,
    label: 'TikTok - THUKHA Industries',
    handle: '@thukhatech',
    url: 'https://www.tiktok.com/@thukhatech',
    color: 'from-red-500 to-pink-500',
  },
  {
    icon: Users,
    label: 'Telegram Movie Channel',
    handle: '@thukhamovies',
    description: 'Tech & Movie Content',
    url: 'https://t.me/thukhamovies',
    color: 'from-indigo-500 to-blue-500',
  },
];

export default function Contact() {
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

    const section = document.getElementById('contact');
    if (section) observer.observe(section);

    return () => observer.disconnect();
  }, []);

  return (
    <section id="contact" className="py-20 relative z-10">
      <div className="container mx-auto px-4">
        <div className="max-w-4xl mx-auto">
          {/* Section title */}
          <div className={`mb-16 transition-all duration-1000 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-10'}`}>
            <h2 className="text-4xl md:text-5xl font-bold glow-text mb-4">Get in Touch</h2>
            <div className="w-20 h-1 bg-gradient-to-r from-primary to-secondary rounded-full" />
          </div>

          {/* Contact description */}
          <div className={`mb-12 text-center transition-all duration-1000 delay-200 ${isVisible ? 'opacity-100' : 'opacity-0'}`}>
            <p className="text-lg text-foreground/70">
              Feel free to reach out through any of these channels. I'm always interested in new projects and collaborations.
            </p>
          </div>

          {/* Primary contact links */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-12">
            {contactLinks.map((link, index) => {
              const Icon = link.icon;
              return (
                <a
                  key={index}
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={`group relative transition-all duration-500 transform ${
                    isVisible
                      ? 'opacity-100 translate-y-0'
                      : 'opacity-0 translate-y-10'
                  }`}
                  style={{
                    transitionDelay: isVisible ? `${index * 100}ms` : '0ms',
                  }}
                >
                  <div className={`absolute inset-0 bg-gradient-to-r ${link.color} rounded-lg opacity-0 group-hover:opacity-100 transition-opacity duration-300 blur-md`} />
                  <div className="relative p-6 rounded-lg border border-border bg-card/80 backdrop-blur-sm hover:border-primary transition-all duration-300 group-hover:scale-105 text-center">
                    <Icon className="w-10 h-10 text-primary mx-auto mb-3" />
                    <h3 className="text-lg font-bold text-foreground mb-2">
                      {link.label}
                    </h3>
                    <p className="text-sm text-foreground/70 break-all">
                      {link.handle}
                    </p>
                  </div>
                </a>
              );
            })}
          </div>

          {/* Social and business links */}
          <div className="space-y-4">
            <h3 className={`text-2xl font-bold text-foreground mb-6 transition-all duration-1000 ${isVisible ? 'opacity-100' : 'opacity-0'}`}>
              Social & Business
            </h3>
            {socialLinks.map((link, index) => {
              const Icon = link.icon;
              return (
                <a
                  key={index}
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={`group block transition-all duration-500 ${
                    isVisible
                      ? 'opacity-100 translate-y-0'
                      : 'opacity-0 translate-y-10'
                  }`}
                  style={{
                    transitionDelay: isVisible ? `${(index + 3) * 100}ms` : '0ms',
                  }}
                >
                  <div className={`relative p-6 rounded-lg border border-border bg-card/50 backdrop-blur-sm hover:border-primary transition-all duration-300 group-hover:bg-card/80`}>
                    <div className="flex items-start gap-4">
                      <Icon className="w-8 h-8 text-primary flex-shrink-0 mt-1" />
                      <div className="flex-1">
                        <h4 className="text-lg font-bold text-foreground mb-1">
                          {link.label}
                        </h4>
                        <p className="text-sm text-primary font-semibold mb-2">
                          {link.handle}
                        </p>
                        {link.description && (
                          <p className="text-sm text-foreground/60">
                            {link.description}
                          </p>
                        )}
                      </div>
                      <div className="text-primary opacity-0 group-hover:opacity-100 transition-opacity duration-300">
                        →
                      </div>
                    </div>
                  </div>
                </a>
              );
            })}
          </div>

          {/* Footer CTA */}
          <div className={`mt-16 p-8 rounded-lg border border-primary/30 bg-gradient-to-r from-primary/5 to-secondary/5 text-center transition-all duration-1000 ${
            isVisible ? 'opacity-100' : 'opacity-0'
          }`}>
            <p className="text-foreground mb-4">
              Let's build something amazing together!
            </p>
            <div className="flex flex-col sm:flex-row gap-4 justify-center">
              <a
                href="https://t.me/thukhasusan78"
                target="_blank"
                rel="noopener noreferrer"
                className="neon-button"
              >
                Message on Telegram
              </a>
              <a
                href="tel:09784679389"
                className="neon-button"
              >
                Call Now
              </a>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
