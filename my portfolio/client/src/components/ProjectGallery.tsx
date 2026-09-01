import { useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';

const galleryItems = [
  {
    id: 1,
    title: 'Bluetooth Jammer Collection',
    description: 'Collection of 5 custom-built Bluetooth Jammer boards with antennas (ESP32-based, PCB boards with multiple antennas)',
    image: '/manus-storage/project_bluetooth_jammers_collection_53bd2330.jpg',
    category: 'Hardware',
  },
  {
    id: 2,
    title: 'Bluetooth Jammer v3 with OLED',
    description: 'Close-up of a single Bluetooth Jammer with OLED display showing signal visualization, 3 antennas, hand-held',
    image: '/manus-storage/project_bluetooth_jammer_oled_5a59b9c2.jpg',
    category: 'Hardware',
  },
  {
    id: 3,
    title: 'DC Power Station',
    description: 'Custom-built DC Power Station with DC Volt Meter display (12.8V), 3x DC 12V outputs with switches, USB 5V port, Power ON/OFF switch',
    image: '/manus-storage/project_power_station_front_4cb9cfa7.jpg',
    category: 'Hardware',
  },
  {
    id: 4,
    title: 'Power Station Internal',
    description: 'Internal view of the Power Station showing lithium battery pack (blue), BMS board, XT60 connector, wiring',
    image: '/manus-storage/project_power_station_internal_3695c805.jpg',
    category: 'Hardware',
  },
  {
    id: 5,
    title: 'ESP32 Remote Control & WiFi Boards',
    description: 'Two ESP32 boards with OLED displays showing "Remote Control" and "WiFi" interfaces, each with 2 antennas',
    image: '/manus-storage/project_esp32_boards_58db6429.jpg',
    category: 'Hardware',
  },
];

export default function ProjectGallery() {
  const [isVisible, setIsVisible] = useState(false);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [autoPlay, setAutoPlay] = useState(true);

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
        }
      },
      { threshold: 0.1 }
    );

    const section = document.getElementById('project-gallery');
    if (section) observer.observe(section);

    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!autoPlay) return;

    const interval = setInterval(() => {
      setCurrentIndex((prev) => (prev + 1) % galleryItems.length);
    }, 5000);

    return () => clearInterval(interval);
  }, [autoPlay]);

  const goToPrevious = () => {
    setAutoPlay(false);
    setCurrentIndex((prev) => (prev - 1 + galleryItems.length) % galleryItems.length);
  };

  const goToNext = () => {
    setAutoPlay(false);
    setCurrentIndex((prev) => (prev + 1) % galleryItems.length);
  };

  const goToSlide = (index: number) => {
    setAutoPlay(false);
    setCurrentIndex(index);
  };

  return (
    <section id="project-gallery" className="py-20 relative z-10">
      <div className="container mx-auto px-4">
        <div className="max-w-6xl mx-auto">
          {/* Section title */}
          <div className={`mb-12 transition-all duration-1000 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-10'}`}>
            <h3 className="text-3xl md:text-4xl font-bold glow-text mb-3">Project Showcase</h3>
            <div className="w-16 h-1 bg-gradient-to-r from-primary to-secondary rounded-full" />
          </div>

          {/* Gallery carousel */}
          <div className={`relative transition-all duration-1000 ${isVisible ? 'opacity-100' : 'opacity-0'}`}>
            {/* Main image */}
            <div className="relative rounded-lg overflow-hidden border border-primary/30 bg-card/50 backdrop-blur-sm group">
              <div className="relative aspect-video overflow-hidden">
                <img
                  src={galleryItems[currentIndex].image}
                  alt={galleryItems[currentIndex].title}
                  className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
                />
                {/* Gradient overlay */}
                <div className="absolute inset-0 bg-gradient-to-t from-black/60 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
              </div>

              {/* Image info */}
              <div className="p-6">
                <div className="inline-block px-3 py-1 rounded-full bg-primary/20 text-primary text-xs font-semibold mb-3">
                  {galleryItems[currentIndex].category}
                </div>
                <h4 className="text-2xl font-bold text-foreground mb-2">
                  {galleryItems[currentIndex].title}
                </h4>
                <p className="text-foreground/70 text-sm">
                  {galleryItems[currentIndex].description}
                </p>
              </div>

              {/* Navigation buttons */}
              <button
                onClick={goToPrevious}
                onMouseEnter={() => setAutoPlay(false)}
                className="absolute left-4 top-1/2 -translate-y-1/2 p-2 rounded-full bg-primary/80 hover:bg-primary text-background transition-all duration-300 opacity-0 group-hover:opacity-100 z-20"
                aria-label="Previous image"
              >
                <ChevronLeft size={24} />
              </button>

              <button
                onClick={goToNext}
                onMouseEnter={() => setAutoPlay(false)}
                className="absolute right-4 top-1/2 -translate-y-1/2 p-2 rounded-full bg-primary/80 hover:bg-primary text-background transition-all duration-300 opacity-0 group-hover:opacity-100 z-20"
                aria-label="Next image"
              >
                <ChevronRight size={24} />
              </button>
            </div>

            {/* Thumbnail navigation */}
            <div className="flex gap-3 mt-6 overflow-x-auto pb-2">
              {galleryItems.map((item, index) => (
                <button
                  key={item.id}
                  onClick={() => goToSlide(index)}
                  onMouseEnter={() => setAutoPlay(false)}
                  className={`flex-shrink-0 w-20 h-20 rounded-lg overflow-hidden border-2 transition-all duration-300 ${
                    index === currentIndex
                      ? 'border-primary scale-110'
                      : 'border-border/50 hover:border-primary/50 opacity-60 hover:opacity-100'
                  }`}
                >
                  <img
                    src={item.image}
                    alt={item.title}
                    className="w-full h-full object-cover"
                  />
                </button>
              ))}
            </div>

            {/* Slide indicators */}
            <div className="flex justify-center gap-2 mt-6">
              {galleryItems.map((_, index) => (
                <button
                  key={index}
                  onClick={() => goToSlide(index)}
                  className={`h-2 rounded-full transition-all duration-300 ${
                    index === currentIndex
                      ? 'bg-primary w-8'
                      : 'bg-primary/30 w-2 hover:bg-primary/60'
                  }`}
                  aria-label={`Go to slide ${index + 1}`}
                />
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
